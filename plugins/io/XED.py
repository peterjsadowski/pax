"""
This plug-in reads raw waveform data from a Xenon100 XED file.
The XED file format is documented in Guillaume Plante's PhD thesis.
This is code does not use the libxdio C-library though.

At the moment this plugin only supports:
    - sequential reading, not searching for a particular event;
    - reading a single XED file, not an entire dataset;
    - one 'chunk' per event, it raises an exception if it sees more than one chunk;
    - zle0 sample encoding, not raw;
    - bzip2 chunk data compression, not gzip, uncompressed data, or whatever else.

None of these would be very difficult to fix, if we ever intend to do any large-scale
reprocessing of the XED-files we have.

Some metadata from the XED file is stored in event['metadata'], see the end of this
code for details.
"""

import math
import bz2
import io
import numpy as np

from pax import plugin, units


class XedInput(plugin.InputPlugin):
    file_header = np.dtype([
        ("dataset_name", "S64"),
        ("creation_time", "<u4"),
        ("first_event_number", "<u4"),
        ("events_in_file", "<u4"),
        ("event_index_size", "<u4")
    ])

    event_header = np.dtype([
        ("dataset_name", "S64"),
        ("utc_time", "<u4"),
        ("utc_time_usec", "<u4"),
        ("event_number", "<u4"),
        ("chunks", "<u4"),
        # This is where the 'chunk layer' starts... but there always seems to be one chunk per event
        # I'll always assume this is true and raise an exception otherwise
        ("type", "S4"),
        ("size", "<u4"),
        ("sample_precision", "<i2"),
        ("flags", "<u2"),  # indicating compression type.. I'll assume bzip2 always
        ("samples_in_event", "<u4"),
        ("voltage_range", "<f4"),
        ("sampling_frequency", "<f4"),
        ("channels", "<u4"),
    ])

    def startup(self):
        self.input = open(self.config['filename'], 'rb')

        # Read metadata and event positions from the XED file
        self.file_metadata = np.fromfile(self.input, dtype=XedInput.file_header, count=1)[0]
        assert self.file_metadata['events_in_file'] == self.file_metadata['event_index_size']
        self.event_positions = np.fromfile(self.input, dtype=np.dtype("<u4"),
                                           count=self.file_metadata['event_index_size'])

    def get_events(self):

        for event_position in self.event_positions:

            # Are we still at the right position in the file?
            if not self.input.tell() == event_position:
                raise ValueError(
                    "Reading error: this event should be at %s, but we are at %s!" % (
                        event_position, self.input.tell()
                    )
                )

            # Read event metadata, check if we can read this.
            event_layer_metadata = np.fromfile(self.input, dtype=XedInput.event_header, count=1)[0]
            if event_layer_metadata['chunks'] != 1:
                raise NotImplementedError(
                    "The day has come: event with %s chunks found!" % event_layer_metadata['chunks'])
            if event_layer_metadata['type'] != b'zle0':
                raise NotImplementedError(
                    "Still have to code grokking for sample type %s..." % event_layer_metadata['type'])
            event = {
                'channel_occurrences': {},
                'length': event_layer_metadata['samples_in_event']
            }

            # Read the channel bitmask to find out which channels are included in this event.
            # Lots of possibilities for errors here: 4-byte groupings, little-endian...
            # Checked (for one event...) agrees with channels from LibXDIO->Moxie->MongoDB->MongoDBInput plugin
            mask_bytes = 4 * int(math.ceil(event_layer_metadata['channels'] / 32))
            mask = np.unpackbits(np.array(list(
                np.fromfile(self.input, dtype=np.dtype('<S%s' % mask_bytes), count=1)[0]
            ), dtype='uint8'))
            channels_included = [i for i, m in enumerate(reversed(mask)) if m == 1]

            # Decompress the event data (actually, the data from a single 'chunk') into fake binary file
            chunk_fake_file = io.BytesIO(bz2.decompress(self.input.read(
                event_layer_metadata['size'] - 28 - mask_bytes)
            ))  # 28 is the chunk header size. TODO: only decompress if needed

            # Loop over all channels in the event
            for channel_id in channels_included:
                event['channel_occurrences'][channel_id] = []

                # Read channel size (in 4bit words), subtract header size, convert from 4-byte words to bytes
                channel_data_size = int(4 * (np.fromstring(chunk_fake_file.read(4), dtype='<u4')[0] - 1))

                # Read the channel data into another fake binary file
                channel_fake_file = io.BytesIO(chunk_fake_file.read(channel_data_size))

                # Read the channel data control word by control word.
                # sample_position keeps track of where in the waveform a new occurrence should be placed.
                sample_position = 0
                while 1:

                    # Is there a new control word?
                    control_word_string = channel_fake_file.read(4)
                    if not control_word_string:
                        break

                    # Control words starting with zero indicate a number of sample PAIRS to skip
                    control_word = int(np.fromstring(control_word_string, dtype='<u4')[0])
                    if control_word < 2 ** 31:
                        sample_position += 2 * control_word
                        continue

                    # Control words starting with one indicate a number of sample PAIRS follow
                    else:
                        data_samples = 2 * (control_word - (2 ** 31))  # Subtract the control word flag
                        samples_occurrence = np.fromstring(channel_fake_file.read(2 * data_samples), dtype="<i2")
                        event['channel_occurrences'][channel_id].append((
                            sample_position,
                            samples_occurrence
                        ))
                        sample_position += len(samples_occurrence)
                        """
                        According to Guillaume's thesis, and the FADC manual, samples come in pairs,
                        with LATER sample first!
                        This would mean we have to ungarble them (split into pairs, reverse the pairs, join again).
                        However, if I try this, it makes peaks come out two-headed...
                        Maybe they were already un-garbled by some previous program??
                        We won't do any ungarbling for now.
                        """

            # Finally, we make some of the Meta data provided in the XED-file available in the event structure
            event['metadata'] = {
                'dataset_name':          self.file_metadata['dataset_name'],
                'dataset_creation_time': self.file_metadata['creation_time'],
                'utc_time':              event_layer_metadata['utc_time'],
                'utc_time_usec':         event_layer_metadata['utc_time_usec'],
                'event_number':          event_layer_metadata['event_number'],
                'voltage_range':         event_layer_metadata['voltage_range'] / units.V,
                'channels_from_input':   event_layer_metadata['channels'],
            }
            yield event

        # If we get here, all events have been read
        self.input.close()