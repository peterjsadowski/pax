"""
Waveform simulator ("FaX") - physics backend
There is no I/O stuff here, all that is in the WaveformSimulator plugins
"""

import numpy as np
import math

import random
from collections import Counter

import logging
log = logging.getLogger('SimulationCore')
from pax import units

global config

def init_config(config_to_init):
    global config
    config = config_to_init
    # Should we repeat events?
    if not 'event_repetitions' in config:
        config['event_repetitions'] = 1

    # Determine sensible length of a pmt pulse to simulate
    dt = config['digitizer_t_resolution']
    config['samples_before_pulse_center'] = math.ceil(
        config['pulse_width_cutoff'] * config['pmt_rise_time'] / dt
    )
    config['samples_after_pulse_center'] = math.ceil(
        config['pulse_width_cutoff'] * config['pmt_fall_time'] / dt
    )

    # Padding on eiher side of event
    if 'event_padding' not in config:
        config['event_padding'] = 0

    # Padding before & after each pulse/peak/photon-cluster/whatever-you-call-it
    if not 'pad_after' in config:
        config['pad_after'] = 30 * dt + config['samples_after_pulse_center']
    if not 'pad_before' in config:
        config['pad_before'] = (
            # 10 + Baseline bins
            50 * dt
            # Protection against early pre-peak rise
            + config['samples_after_pulse_center']
            # Protection against pulses arriving earlier than expected due
            # to tail of TTS distribution
            + 10 * config['pmt_transit_time_spread']
            - config['pmt_transit_time_mean']
        )

    # Temp hack: need 0 in channels so we can use lists... hmmzzz
    config['channels'] = list({0} | config['pmts_top'] | config['pmts_bottom'])

    return config


@np.vectorize
def exp_pulse(t, q, tr, tf):
    """Integrated current (i.e. charge) of a single-pe PMT pulse centered at t=0
    Assumes an exponential rise and fall waveform model
    :param t:   Time to integrate up to
    :param q:   Total charge in the pulse
    :param tr:  Rise time
    :param tf:  Fall time
    :return: Float, charge deposited up to t
    """
    c = 0.45512  # 1/(ln(10)-ln(10/9))
    if t < 0:
        return q / (tr + tf) * (tr * math.exp(t / (c * tr)))
    else:
        return q / (tr + tf) * (tr + tf * (1 - math.exp(-t / (c * tf))))
    

def s2_electrons(electrons_generated=None, z=0., t=0.):
    """Return a list of electron arrival times in the ELR region caused by an S2 process.

        electrons             -   total # of drift electrons generated at the interaction site
        t                     -   Time at which the original energy deposition occurred.
        z                     -   Depth below the GATE mesh where the interaction occurs.
    As usual, all units in the same system used by pax (if you specify raw values: ns, cm)
    """

    if z < 0:
        log.warning("Unphysical depth: %s cm below gate. Not generating S2." % z)
        return []
    log.debug("Creating an s2 from %s electrons..." % electrons_generated)

    # Average drift time, taking faster drift velocity after gate into account
    drift_time_mean = z / config['drift_velocity_liquid'] + \
        (config['gate_to_anode_distance'] - config['elr_gas_gap_length']) \
        / config['drift_velocity_liquid_above_gate']

    # Diffusion model from Sorensen 2011
    drift_time_stdev = math.sqrt(
        2 * config['diffusion_constant_liquid'] *
        drift_time_mean / (config['drift_velocity_liquid']) ** 2
    )

    # Absorb electrons during the drift
    electrons_seen = np.random.binomial(
        n= electrons_generated,
        p= config['electron_extraction_yield']
           * math.exp(- drift_time_mean / config['electron_lifetime_liquid'])
    )
    log.debug("    %s electrons survive the drift." % electrons_generated)

    # Calculate electron arrival times in the ELR region
    e_arrival_times = t + np.random.exponential(config['electron_trapping_time'], electrons_seen)
    if drift_time_stdev:
        e_arrival_times += np.random.normal(drift_time_mean, drift_time_stdev, electrons_seen)
    return e_arrival_times


def s1_photons(n_photons, t=0, recombination_time=None, singlet_fraction=None, primary_excimer_fraction=None):
    """
    Returns a list of photon production times caused by an S1 process.

    """

    # Fill in optional arguments
    if recombination_time is None:
        recombination_time = config['s1_default_recombination_time']
    if singlet_fraction is None:
        singlet_fraction = config['s1_default_singlet_fraction']
    if primary_excimer_fraction is None:
        primary_excimer_fraction = config['s1_default_primary_excimer_fraction']

    # Apply detection efficiency
    log.debug("Creating an s1 from %s photons..." % n_photons)
    n_photons = np.random.binomial(n=n_photons, p=config['s1_detection_efficiency'])
    log.debug("    %s photons are detected." % n_photons)
    if n_photons == 0:
        return np.array([])

    # How many of these are primary excimers? Others arise through recombination.
    n_primaries = np.random.binomial(n=n_photons, p=primary_excimer_fraction)

    # Handle recombination delays
    photon_times = t + np.concatenate([
        np.zeros(n_primaries),
        recombination_time * (1 / np.random.uniform(0, 1, n_photons - n_primaries) - 1)
    ])

    # Account for singlet/triplet decay times
    timings = singlet_triplet_delays(
        photon_times,
        t1=config['singlet_lifetime_liquid'],
        t3=config['triplet_lifetime_liquid'],
        singlet_ratio=singlet_fraction
    )

    return timings


def s2_scintillation(electron_arrival_times):
    """
    Given a list of electron arrival times, returns photon production times
    """

    # How many photons does each electron make?
    # TODO: xy correction!
    photons_produced = np.random.poisson(
        config['s2_secondary_sc_yield_density'] * config['elr_gas_gap_length'],
        len(electron_arrival_times)
    )
    total_photons = np.sum(photons_produced)
    log.debug("    %s scintillation photons will be detected." % total_photons)
    if total_photons == 0:
        return np.array([])

    # Find the photon production times
    # Assume luminescence probability ~ electric field
    s2_pe_times = np.concatenate([
        t0 + get_luminescence_positions(photons_produced[i]) / config['drift_velocity_gas']
        for i, t0 in enumerate(electron_arrival_times)
    ])

    # Account for singlet/triplet excimer decay times
    return singlet_triplet_delays(
        s2_pe_times,
        t1=config['singlet_lifetime_gas'],
        t3=config['triplet_lifetime_gas'],
        singlet_ratio=config['singlet_fraction_gas']
    )


def singlet_triplet_delays(times, t1, t3, singlet_ratio):
    """
    Given a list of eximer formation times, returns excimer decay times.
        t1            - singlet state lifetime
        t3            - triplet state lifetime
        singlet_ratio - fraction of excimers that become singlets
                        (NOT the ratio of singlets/triplets!)
    """
    n_singlets = np.random.binomial(n=len(times), p=singlet_ratio)
    return times + np.concatenate([
        np.random.exponential(t1, n_singlets),
        np.random.exponential(t3, len(times) - n_singlets)
    ])


def get_luminescence_positions(n):
    """Sample luminescence positions in the ELR, using a mixed wire-dominated / uniform field"""
    x = np.random.uniform(0, 1, n)
    l = config['elr_gas_gap_length']
    wire_par = config['wire_field_parameter']
    rm = config['anode_mesh_pitch'] * wire_par
    rw = config['anode_wire_radius']
    if wire_par == 0:
        return x * l
    totalArea = l + rm * (math.log(rm / rw) - 1)
    relA_wd_region = rm * math.log(rm / rw) / totalArea
    # AARRGHH! Should vectorize...
    return np.array([
        (l - np.exp(xi * totalArea / rm) * rw)
        if xi < relA_wd_region
        else l - (xi * totalArea + rm * (1 - math.log(rm / rw)))
        for xi in x
    ])


def photons_to_hitlist(photon_timings, x=0., y=0., z=0.):
    """Compute photon arrival time list ('hitlist') from photon production times

    :param photon_timings: list of times at which photons are produced at this position
    :param x: x-coordinate of photon production site
    :param y: y-coordinate of photon production site
    :param z: z-coordinate of photon production site
    :return: numpy array, indexed by pmt number, of numpy arrays of photon arrival times
    """

    # TODO: Use light collection map to divide photons
    # TODO: if positions unspecified, pick a random position (useful for poisson noise)

    # So channel 1 doesn't always get the first photon...
    random.shuffle(photon_timings)

    # Determine how many photons each pmt gets
    # TEMP - Uniformly distribute photons over all PMTs!
    # TEMP - hack to prevent photons getting into the ghost channel 0
    channels_for_photons = list(set(config['channels']) - set([0]))
    if 'magically_avoid_dead_pmts' in config and config['magically_avoid_dead_pmts']:
        channels_for_photons = [ch for ch in channels_for_photons if config['gains'][ch] > 0]
    if 'magically_avoid_s1_excluded_pmts' in config and config['magically_avoid_dead_pmts']:
        channels_for_photons = [ch for ch in channels_for_photons if not ch in config['pmts_excluded_for_s1']]
    hit_counts = Counter([
        random.choice(channels_for_photons)
        for _ in photon_timings
    ])

    # Make the hitlist, a numpy array, so we can add it elementwise so we
    # can add them
    hitlist = []
    already_used = 0
    for ch in config['channels']:
        hitlist.append(sorted(photon_timings[already_used:already_used + hit_counts[ch]]))
        already_used += hit_counts[ch]
    log.debug("    %s hits in hitlist" % sum([len(x) for x in hitlist]))

    # TODO: factor in propagation time
    return np.array(hitlist)


def pmt_pulse_current(gain, offset=0):
    dt = config['digitizer_t_resolution']
    return np.diff(exp_pulse(
        np.linspace(
            - offset - config['samples_before_pulse_center'] * dt,
            - offset + config['samples_after_pulse_center']  * dt,
            1 + config['samples_after_pulse_center'] + config['samples_before_pulse_center']
        ),
        gain * units.electron_charge,
        config['pmt_rise_time'],
        config['pmt_fall_time']
    )) / dt


def hitlist_to_waveforms(hitlist):
    """Simulate PMT response to incoming photons
    Returns None if you pass a hitlist without any hits
    """
    # TODO: Account for random initial digitizer state  wrt interaction?
    # Where?

    # Convenience variables
    dt = config['digitizer_t_resolution']
    dV = config['digitizer_voltage_range'] / 2 ** (config['digitizer_bits'])
    pad_before = config['pad_before']
    pad_after = config['pad_after']

    # Compute waveform start, length, end
    all_photons = flatten(hitlist)
    if not all_photons:
        return None
    start_time = min(all_photons) - pad_before
    n_samples = math.ceil((max(all_photons) + pad_after - start_time) / dt)\
                + 2 \
                + config['samples_after_pulse_center']

    # Build waveform channel by channel
    pmt_waveforms = np.zeros((len(hitlist), n_samples), dtype=np.int16)
    for channel, photon_detection_times in enumerate(hitlist):
        if len(photon_detection_times) == 0:
            continue  # No photons in this channel

        # Correct for PMT transit time, subtract start_time, and (re-)sort
        pmt_pulse_centers = np.sort(
            photon_detection_times - start_time + np.random.normal(
                config['pmt_transit_time_mean'],
                config['pmt_transit_time_spread'],
                len(photon_detection_times)
            )
        )

        # Build the waveform pulse by pulse (bin by bin was slow, hope this
        # is faster)

        # Compute offset & center index for each pulse
        offsets = pmt_pulse_centers % dt
        center_index = (pmt_pulse_centers - offsets) / dt
        if len(all_photons) > config['use_simplified_simulator_from']:

            # Start with a delta function single photon pulse, then convolve with one actual single-photon pulse
            # This effectively assumes photons always arrive at the start of a digitizer t-bin, but is much faster
            pulse_counts = Counter(center_index)
            current_wave = np.array([pulse_counts[n] for n in range(n_samples)]) \
                           * config['gains'][channel] * units.electron_charge / dt

            # Calculate a normalized pmt pulse, for use in convolution later (only
            # for large peaks)
            normalized_pulse = pmt_pulse_current(gain=1)
            normalized_pulse /= np.sum(normalized_pulse)
            current_wave = np.convolve(current_wave, normalized_pulse, mode='same')

        else:

            # Do the full, slower simulation for each single-photon pulse
            current_wave = np.zeros(n_samples)
            for i, t0 in enumerate(pmt_pulse_centers):

                # Add some current for this photon pulse
                # Compute the integrated pmt pulse at various samples, then
                # do their diffs/dt

                current_wave[
                    # +1 due to np.diff in pmt_pulse_current
                    center_index[i] - config['samples_before_pulse_center'] + 1 :
                    center_index[i] + 1 + config['samples_after_pulse_center']
                ] += pmt_pulse_current(
                    # Really a Poisson (although mean is so high it is very
                    # close to a Gauss)
                    gain=np.random.poisson(config['gains'][channel]),
                    offset=offsets[i]
                )

        # Add white noise current - only to channels that have seen a photon, only around a peak
        current_wave += np.random.normal(0, config['white_noise_sigma'], len(current_wave))

        # Convert current to digitizer count (should I trunc, ceil or floor?) and store
        # Don't baseline correct, clip or flip down here, we do that at the
        # very end when all signals are combined
        temp = np.trunc(
            config['pmt_circuit_load_resistor'] 
            * config['external_amplification'] / dV 
            * current_wave
        )
        pmt_waveforms[channel] = temp.astype(np.int16)

    return start_time, pmt_waveforms


# This is probably in some standard library...
def flatten(l):
    return [item for sublist in l for item in sublist]