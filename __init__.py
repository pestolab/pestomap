def classFactory(iface):
    from .pestomap_plugin import PestoMapPlugin
    return PestoMapPlugin(iface)
