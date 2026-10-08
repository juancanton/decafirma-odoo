{
    'name': 'DecaFirma Delivery Routes',
    'version': '17.0.1.0.0',
    'category': 'Inventory/Delivery',
    'summary': 'A batch of outgoing transfers is a delivery route in DecaFirma: one DeCA for all the stops',
    'description': """
DecaFirma Delivery Routes
=========================
A batch transfer (Inventory > Batch Transfers) is issued as a route: a single
DeCA for all the stops (same shipper and same carrier, Resolution of 5 June
2026, section six) and, optionally, one delivery note per stop signed by
whoever receives the goods. Adding stops issues the DeCA as a new version with
the same QR code; removing them, too.
""",
    'author': 'Francodesystems',
    'website': 'https://decafirma.com',
    'support': 'juan.canton@francodesystems.com',
    'images': ['static/description/cover.png', 'static/description/ruta.png'],
    'license': 'OPL-1',
    'depends': ['decafirma_stock', 'stock_picking_batch'],
    'data': ['views/stock_picking_batch_views.xml'],
    'installable': True,
    'auto_install': True,
}
