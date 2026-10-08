import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .client import DecafirmaClient, DecafirmaError
from .decafirma_mixin import comprobar_permiso

_logger = logging.getLogger(__name__)

TIPOS = [
    ('albaran', 'Albarán'),
    ('carta_de_porte', 'Carta de porte'),
    ('ticket_bascula', 'Ticket de báscula'),
    ('ticket', 'Ticket'),
    ('otro', 'Otro papel'),
]
EXTENSIONES = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'application/pdf': 'pdf'}


class DecafirmaPapel(models.Model):
    """Un papel del porte: la foto que hace el conductor a la carta de porte o al ticket de báscula.

    DecaFirma lee de la foto el número y el peso neto. Aquí llega con el envío
    (por un aviso o al ponerse al día), la foto se adjunta al registro de origen
    y se marca como facturado para no facturarlo dos veces, en Odoo y en DecaFirma.
    Solo con los planes de DecaFirma que llevan papeles: si no, no llega ninguno.
    """
    _name = 'decafirma.papel'
    _description = 'Papel del porte (DecaFirma)'
    _order = 'subido_at desc, id desc'
    _rec_name = 'numero'

    shipment_id = fields.Many2one('decafirma.shipment', 'Envío', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='shipment_id.company_id', store=True)
    deca_id = fields.Char('Id en DecaFirma', required=True, index=True)
    tipo = fields.Selection(TIPOS, 'Tipo')
    numero = fields.Char('Número')
    emisor = fields.Char('Emisor')
    fecha = fields.Char('Fecha del papel')
    peso_neto_kg = fields.Float('Peso neto (kg)', digits=(12, 2))
    leido = fields.Boolean('Leído', help='DecaFirma ha podido leer el número y el peso de la foto.')
    mime = fields.Char('Tipo de archivo')
    subido_por = fields.Char('Subido por')
    subido_at = fields.Datetime('Subido el')
    facturado_at = fields.Datetime('Facturado el')
    facturado = fields.Boolean('Facturado', compute='_compute_facturado', store=True)
    archivo_url = fields.Char('Archivo')
    attachment_id = fields.Many2one('ir.attachment', 'Foto', ondelete='set null')
    origen_ref = fields.Char('Origen', related='shipment_id.name')

    _deca_id_uniq = models.Constraint('UNIQUE(shipment_id, deca_id)', 'Ese papel de DecaFirma ya está en el envío.')

    @api.depends('facturado_at')
    def _compute_facturado(self):
        for p in self:
            p.facturado = bool(p.facturado_at)

    @staticmethod
    def _valores(data, fecha):
        peso = data.get('pesoNetoKg')
        return {
            'deca_id': data['id'],
            # Lo que no se pudo leer llega sin tipo; uno que aún no conocemos, como «otro».
            'tipo': (data.get('tipo') if data.get('tipo') in dict(TIPOS) else 'otro') if data.get('tipo') else False,
            'numero': data.get('numero') or False,
            'emisor': data.get('emisor') or False,
            'fecha': data.get('fecha') or False,
            'peso_neto_kg': float(peso) if peso not in (None, '') else 0.0,
            'leido': bool(data.get('leido')),
            'mime': data.get('mime') or False,
            'subido_por': data.get('subidoPor') or False,
            'subido_at': fecha(data.get('subido')),
            'facturado_at': fecha(data.get('facturado')),
            'archivo_url': data.get('archivo') or False,
        }

    def _nombre_archivo(self):
        self.ensure_one()
        ext = EXTENSIONES.get(self.mime or '', 'bin')
        return f'{self._tipo_texto()} {self.numero or self.deca_id}.{ext}'.replace('/', '-')

    def _tipo_texto(self):
        return dict(self._fields['tipo']._description_selection(self.env)).get(self.tipo) or _('Papel del porte')

    def _adjuntar(self):
        """Baja la foto (o el PDF) y la deja adjunta en el registro de origen, una sola vez."""
        self.ensure_one()
        if self.attachment_id or not self.archivo_url:
            return self.attachment_id
        content = DecafirmaClient(self.company_id).archivo(self.archivo_url)
        origen = self.shipment_id._origen()
        attachment = self.env['ir.attachment'].sudo().create({
            'name': self._nombre_archivo(),
            'raw': content,
            'mimetype': self.mime or 'application/octet-stream',
            'res_model': origen._name if origen else self.shipment_id._name,
            'res_id': origen.id if origen else self.shipment_id.id,
        })
        self.sudo().attachment_id = attachment
        return attachment

    def _contar_llegada(self):
        """El papel recién llegado, en el chatter del origen con su foto."""
        self.ensure_one()
        origen = self.shipment_id._origen()
        if not origen or not hasattr(origen, 'message_post'):
            return
        adjuntos = []
        try:
            adjuntos = self._adjuntar().ids
        except (UserError, DecafirmaError) as e:
            # Sin la foto se cuenta igual; «Ver la foto» en la línea la vuelve a pedir.
            _logger.warning('DecaFirma: no se ha podido bajar el papel %s: %s', self.deca_id, e)
        detalle = ', '.join(filter(None, [
            self.numero and _('n.º %s', self.numero),
            self.peso_neto_kg and _('%s kg netos', f'{self.peso_neto_kg:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')),
        ])) or _('sin leer')
        origen.message_post(
            body=_('Papel del porte recibido (%(tipo)s): %(detalle)s. Lo subió %(quien)s.',
                   tipo=self._tipo_texto().lower(), detalle=detalle, quien=self.subido_por or _('el conductor')),
            attachment_ids=adjuntos)

    # ------------------------------------------------------------------
    # Botones
    # ------------------------------------------------------------------

    def action_ver_foto(self):
        self.ensure_one()
        try:
            attachment = self._adjuntar()
        except DecafirmaError as e:
            raise UserError(str(e)) from e
        if not attachment:
            raise UserError(_('Este papel no tiene archivo.'))
        return {'type': 'ir.actions.act_url', 'url': f'/web/content/{attachment.id}?download=false', 'target': 'new'}

    def _marcar(self, facturado):
        comprobar_permiso(self.env)
        for p in self:
            if p.facturado == facturado:
                continue
            try:
                _code, data = DecafirmaClient(p.company_id).marcar_papel(p.deca_id, facturado)
            except DecafirmaError as e:
                raise UserError(str(e)) from e
            if data and 'facturado' in data:
                cuando = p.shipment_id._fecha(data['facturado'])
            else:
                cuando = fields.Datetime.now() if facturado else False
            p.sudo().facturado_at = cuando
        return True

    def action_marcar_facturado(self):
        return self._marcar(True)

    def action_marcar_pendiente(self):
        return self._marcar(False)
