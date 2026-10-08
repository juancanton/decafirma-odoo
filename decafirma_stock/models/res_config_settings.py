from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    decafirma_cargador = fields.Selection(related='company_id.decafirma_cargador', readonly=False)
    decafirma_auto = fields.Selection(related='company_id.decafirma_auto', readonly=False)
