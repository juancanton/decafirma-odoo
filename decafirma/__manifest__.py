{
    'name': 'DecaFirma Connector',
    'version': '18.0.1.0.0',
    'category': 'Inventory/Delivery',
    'summary': 'Connector with DecaFirma: issue the Spanish electronic control document (DeCA) with its QR code and delivery notes signed on a phone',
    'description': """
DecaFirma Connector (decafirma.com)
===================================
Common base to issue from Odoo the electronic control document (DeCA),
mandatory for road freight in Spain from 5 October 2026 (Orden FOM/2861/2012
and Resolution of 5 June 2026), and the delivery note the customer signs on
their phone.

* Settings: the DecaFirma API key and the webhooks, set up with one button.
* One record per DecaFirma shipment, linked to its origin (an Inventory
  transfer, a TMS service...), with its documents: QR code, version, status,
  PDF and who signed.
* Sign Odoo's own delivery slip: the usual PDF goes out, the customer signs it
  on their phone and it comes back signed, attached to the transfer, with the
  signer in the chatter and an activity if they signed with remarks.
* Access rights as in DecaFirma: "DecaFirma / Usuario" (user) issues, corrects and
  sends; "DecaFirma / Responsable" (manager) can also cancel. DecaFirma shows who did it.
* Transport papers: the photo of the consignment note or weighbridge ticket,
  with its number and net weight read by DecaFirma, attached to the origin
  and marked as invoiced in Odoo and DecaFirma (plans with papers).
* Stays up to date: DecaFirma webhooks (signed, new version, cancelled)
  arrive instantly, and a scheduled action catches up every 15 minutes.

Data sent to DecaFirma: nothing until you enter your own API key. Then, when
a document is issued, corrected, cancelled or sent, the data the DeCA requires
(names, tax IDs and addresses of company, customer, carrier and driver,
plates, products and weights, and the delivery slip PDF to be signed).

Used by `decafirma_stock` (outgoing transfers and batches) and `tms_deca`.
Requires a DecaFirma account with API access (paid plans).
""",
    'author': 'Francodesystems',
    'website': 'https://decafirma.com',
    'support': 'juan.canton@francodesystems.com',
    'images': ['static/description/cover.png', 'static/description/ajustes.png'],
    'license': 'OPL-1',
    'depends': ['mail'],
    'external_dependencies': {'python': ['requests']},
    'data': [
        'security/decafirma_groups.xml',
        'security/ir.model.access.csv',
        'security/decafirma_security.xml',
        'data/ir_cron.xml',
        'wizard/decafirma_wizard_views.xml',
        'views/decafirma_shipment_views.xml',
        'views/decafirma_papel_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
}
