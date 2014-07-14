import logging
import argparse
import inspect
import os

from pluginbase import PluginBase
from confiture import Confiture
from confiture.schema import ValidationError
from confiture.parser import ParsingError
from pax.configuration import PaxSchema


def Instantiate(name, plugin_source, config_values):
    """take class name and build class from it"""
    name_module, name_class = name.split('.')
    plugin_module = plugin_source.load_plugin(name_module)
    return getattr(plugin_module, name_class)(config_values)


def Processor(input, transform, output):
    # Check input types
    # TODO (tunnell): Don't use asserts, but raise ValueError() with
    # informative error
    assert isinstance(input, str)
    assert isinstance(transform, (str, list))
    assert isinstance(output, (str, list))

    # If 'transform' or 'output' aren't lists, turn them into lists
    if not isinstance(transform, list):
        transform = [transform]
    if not isinstance(output, list):
        output = [output]

    # What we do on data...
    list_of_actions = transform + output

    # Populate command-line argument parser and fetch values into configuration
    argument_parser = argparse.ArgumentParser()
    schema = PaxSchema()
    x = schema.populate_argparse(argument_parser)  # Fetch command line args
    args = argument_parser.parse_args()

    # We have to do two passes on the configuration: the first pass searches
    # to see if a configuration file (i.e., .ini) was specified.
    config = Confiture("",
                       schema=schema)
    pconfig = config.parse()
    config_values = pconfig.to_dict()

    if config_values['config']: pass

    print('cv', config_values['config'])

    config = Confiture("", schema=schema)
    pconfig = config.parse()
    config_values = pconfig.to_dict()

    # try:
    #     pconfig = config.parse()
    # except (ValidationError, ParsingError) as err:
    #     if err.position is not None:
    #         print(str(err.position))
    #     print(err)
    #     raise
    # else:
    #     config_values = pconfig.to_dict()

    # Setup logging
    numeric_level = getattr(logging, config_values['loglevel'].upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError('Invalid log level: %s' % config_values['loglevel'])
    FORMAT = '%(asctime)-15s %(name)s L%(lineno)s - %(levelname)s %(message)s'
    logging.basicConfig(level=numeric_level, format=FORMAT)
    log = logging.getLogger('Processor')

    # Setup plugins (which involves finding the plugin directory.
    plugin_base = PluginBase(package='pax.plugins')
    searchpath = ['./plugins'] + config_values['plugin_paths']

    # Find the absolute path, then director, then find plugin directory
    absolute_path = os.path.abspath(inspect.getfile(inspect.currentframe()))
    dir = os.path.dirname(absolute_path)
    searchpath += [os.path.join(dir, '..', 'plugins')]
    log.debug("Search path for plugins is %s" % str(searchpath))
    plugin_source = plugin_base.make_plugin_source(searchpath=searchpath)

    # Instantiate requested plugins
    input = Instantiate(input, plugin_source, config_values)
    list_of_actions = [Instantiate(x, plugin_source, config_values) for x in list_of_actions]

    # This is the *actual* event loop
    for event in input.GetEvents():
        for i, block in enumerate(list_of_actions):
            log.info("Step %d with %s", i, block.__class__.__name__)
            event = block.ProcessEvent(event)
