import matplotlib.pyplot as plt
import random
import numpy as np

from pax import plugin


class PlottingWaveform(plugin.OutputPlugin):
    def write_event(self, event):
        """Plot an event

        Will make a fancy plot with lots of arrows etc of a summed waveform
        """
        fig = plt.figure()
        ax = fig.add_subplot(111)

        # Plot all peaks
        max_y = max([p.peak_dict['height'] for p in event.S2s() + event.S1s()])

        for peak in event.S2s() + event.S1s():

            x = peak.peak_dict['index_of_max_in_waveform']
            y = peak.peak_dict['height']

            plt.hlines(y, *peak.bounds())
            ax.annotate('%s:%s' % (peak.__class__.__name__, int(peak.area())),
                        xy=(x, y),
                        xytext=(x, y + (max_y-y)*(0.05+0.2*random.random())),
                        arrowprops=dict(arrowstyle="fancy",
                                        fc="0.6", ec="none",
                                        connectionstyle="angle3,angleA=0,angleB=-90"))

        plt.plot(event.filtered_waveform('uncorrected_sum_waveform_for_s1'), label='S1 peakfinding')
        plt.plot(event.filtered_waveform('uncorrected_sum_waveform_for_s2'), label='S2 peakfinding')
        plt.plot(event.filtered_waveform('filtered_for_large_s2'), label='Large s2 filtered')
        plt.plot(event.filtered_waveform('filtered_for_small_s2'), label='Small s2 filtered')
        event_length = len(event.summed_waveform())-1 #Want this as field in event structure...
        plt.plot([0.6241506363] * event_length,  '--', label='Large S2 threshold')
        plt.plot([0.06241506363]* event_length,  '--', label='Small S2 threshold')
        plt.plot([0.1872451909] * event_length,  '--', label='S1 threshold')

        legend = plt.legend(loc='upper left', prop={'size':10})
        legend.get_frame().set_alpha(0.5)
        plt.xlabel('Sample in event [10 ns]')
        plt.ylabel("pe / bin")
        plt.tight_layout()

        plt.show(block=False)
        self.log.info("Hit enter to continue...")
        input()
        plt.close()


class PlottingHitPattern(plugin.OutputPlugin):
    def startup(self):
        self.topArrayMap = self.config['topArrayMap']

    def write_event(self, event):
        """Plot an event

        Will make a fancy plot with lots of arrows etc of a summed waveform
        """
        plt.figure()

        x = []
        y = []
        area = []

        for pmt, pmt_location in self.topArrayMap.keys():
            q = np.sum(event.pmt_waveform(pmt))

            x.append(pmt_location['x'])
            y.append(pmt_location['y'])
            area.append(q)

        area = np.array(area) / 10

        c = plt.scatter(x, y, c='red',
                        s=area, cmap=plt.cm.hsv)
        c.set_alpha(0.75)

        plt.show(block=False)

        self.log.info("Hit enter to continue...")
        input()