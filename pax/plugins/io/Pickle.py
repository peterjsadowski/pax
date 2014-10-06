"""Read/write event class from/to gzip-compressed pickle files.
"""

from pax import plugin

import gzip, re, glob
try:
    import cPickle as pickle
except:
    import pickle


class WriteToPickleFile(plugin.OutputPlugin):

    def write_event(self, event):
        self.log.debug("Starting pickling...")
        with gzip.open(self.config['output_dir'] + '/' + str(event.event_number), 'wb', compresslevel=1) as file:
            pickle.dump(event, file)
        self.log.debug("Done!")



class DirWithPickleFiles(plugin.InputPlugin):

    def startup(self):
        files = glob.glob(self.config['input_dir'] + "/*")
        self.event_files = {}
        if len(files)==0:
            self.log.fatal("No files found in input directory %s!" % self.config['input_dir'])
        for file in files:
            m = re.search('(\d+)$',file)
            if m is None:
                self.log.debug("Invalid file %s" % file)
                continue
            else:
                self.event_files[int(m.group(0))] = file

    def get_single_event(self, index):
        file = self.event_files[index]
        with gzip.open(file,'rb') as f:
            return pickle.load(f)

    def get_events(self):
        for index in sorted(self.event_files.keys()):
            yield self.get_single_event(index)