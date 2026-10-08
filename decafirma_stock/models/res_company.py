from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    decafirma_cargador = fields.Selection(
        [('empresa', 'Nuestra empresa'), ('cliente', 'El cliente del albarán')],
        'Cargador del DeCA', default='empresa',
        help='Quien contrata el transporte y figura como cargador en el DeCA. Si vendéis y lleváis la '
             'mercancía, sois vosotros; si el cliente os contrata el porte, el cliente. Se puede cambiar en cada albarán.')
    decafirma_auto = fields.Selection(
        [('no', 'No, con el botón'), ('deca', 'El DeCA'), ('ambos', 'El DeCA y el albarán')],
        'Emitir al validar', default='no',
        help='Al validar un albarán de salida se emite solo en DecaFirma. Si no se puede, el albarán se valida '
             'igual y el motivo queda en su chatter.')
