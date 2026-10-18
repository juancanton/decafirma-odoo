{
    'name': 'DecaFirma Fleet',
    'version': '17.0.1.1.0',
    'category': 'Inventory/Delivery',
    'summary': 'Pick the truck and trailer from Fleet for the Spanish DeCA: plate and driver filled in',
    'description': """
DecaFirma Fleet
===============
For DecaFirma Inventory users who keep their trucks in Fleet:

* On the outgoing transfer and on the batch (delivery route), choose the
  vehicle from Fleet: its plate and its driver go into the DeCA.
* The trailer, another Fleet vehicle: its plate goes into the DeCA.
* The plate can still be edited by hand: it is what the DeCA carries.
* Set-up ("Puesta a punto" in Settings > DecaFirma): the Fleet vehicles with
  their driver, and the owner-drivers (contacts ticked as "Transportista
  autónomo") with their truck, go to DecaFirma as vehicles, drivers and carriers.

Installed automatically when DecaFirma Delivery Routes and Fleet are both
installed; it can also be installed by hand (it brings Batch Transfers).
When issuing, only the plate and the driver are sent, as before.
""",
    'author': 'Francodesystems',
    'website': 'https://decafirma.com',
    'support': 'juan.canton@francodesystems.com',
    'images': ['static/description/cover.png'],
    'license': 'OPL-1',
    'depends': ['decafirma_stock_batch', 'fleet'],
    'data': ['views/decafirma_fleet_views.xml'],
    'installable': True,
    'auto_install': True,
}
