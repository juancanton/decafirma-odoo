from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # Un autónomo trabaja para vosotros con su camión: no es vuestro chófer ni vuestro camión.
    # En DecaFirma, un transportista («carrier») con su vehículo.
    decafirma_autonomo = fields.Boolean(
        'Transportista autónomo',
        help='Lleva portes para vosotros con su propio camión. En la puesta a punto de DecaFirma va como '
             'transportista, con su camión: el que se ponga aquí o el de Flota del que sea conductor.')
    decafirma_autonomo_matricula = fields.Char(
        'Matrícula de su camión', help='Si su camión no está en Flota. Si está, basta con que sea su conductor.')
