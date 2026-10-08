{
    'name': 'DecaFirma Inventory',
    'version': '18.0.1.0.0',
    'category': 'Inventory/Delivery',
    'summary': 'Issue the Spanish DeCA and the signed delivery note from the outgoing transfer in Inventory',
    'description': """
DecaFirma Inventory
===================
For companies that sell and deliver with their own trucks (or a carrier) and
already have their transfers in Odoo:

* On the outgoing transfer: "Emitir DeCA", "Emitir albarán" or both, with the
  customer, the warehouse, the products and their weight as they are in Odoo.
* Plate, trailer and driver on the transfer itself; carrier and shipper, if
  they are not you.
* QR code, signing link, PDF and signer on the transfer; the signature
  notification arrives in the chatter on its own.
* Correct (new version with the same QR code), cancel and send to the driver.
* Optional: issue only when the transfer is validated.
* Transport papers (photo, number and net weight) on the transfer and in
  Inventory > Papeles del porte, ready to invoice.
* With Batch Transfers (stock_picking_batch), delivery routes:
  `decafirma_stock_batch` is installed automatically.

Data sent to DecaFirma: nothing until you enter your own API key. Then the
data the DeCA requires (names, tax IDs and addresses, plates, products and
weights).
""",
    'author': 'Francodesystems',
    'website': 'https://decafirma.com',
    'support': 'juan.canton@francodesystems.com',
    'images': ['static/description/cover.png', 'static/description/albaran.png'],
    'license': 'OPL-1',
    'depends': ['decafirma', 'stock'],
    'data': [
        'views/res_config_settings_views.xml',
        'views/stock_picking_views.xml',
    ],
    'installable': True,
}
