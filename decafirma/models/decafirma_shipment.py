import logging
from datetime import datetime, timedelta, timezone

from dateutil import parser as dateparser

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .client import DecafirmaClient, DecafirmaError
from .decafirma_mixin import comprobar_permiso

_logger = logging.getLogger(__name__)

ESTADOS = {
    'borrador': 'draft',
    'documentado': 'documented',
    'entregado': 'delivered',
    'cancelado': 'cancelled',
}


class DecafirmaShipment(models.Model):
    """Un envío de DecaFirma y lo que lo origina en Odoo (un albarán, un servicio del TMS…).

    Va enlazado por modelo e id, como los adjuntos, para que cualquier módulo
    pueda emitir sin tocar este. Lo que manda DecaFirma (por la API, por un
    aviso o al ponerse al día) entra siempre por `_aplicar`.
    """
    _name = 'decafirma.shipment'
    _description = 'Envío de DecaFirma'
    _inherit = ['mail.thread']
    _order = 'id desc'
    _rec_name = 'name'

    name = fields.Char('Referencia', required=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    res_model = fields.Char('Modelo de origen', required=True, index=True)
    res_id = fields.Many2oneReference('Registro de origen', model_field='res_model', required=True, index=True)
    deca_id = fields.Char('Id del envío en DecaFirma', index=True, copy=False)
    route_deca_id = fields.Char('Id de la ruta en DecaFirma', index=True, copy=False)
    route_stop = fields.Integer('Parada')
    route_deca_url = fields.Char('QR del DeCA de la ruta', help='En una ruta, el DeCA es uno para todas las paradas.')
    state = fields.Selection([
        ('none', 'Sin emitir'),
        ('draft', 'Creado, sin documento'),
        ('documented', 'Documentado'),
        ('delivered', 'Entregado'),
        ('cancelled', 'Cancelado'),
    ], 'Estado', default='none', tracking=True)
    error = fields.Char('Último aviso de DecaFirma')
    document_ids = fields.One2many('decafirma.document', 'shipment_id', 'Documentos')
    papel_ids = fields.One2many('decafirma.papel', 'shipment_id', 'Papeles del porte')
    deca_url = fields.Char('QR del DeCA', compute='_compute_enlaces')
    albaran_sign_url = fields.Char('Enlace de firma', compute='_compute_enlaces')
    signed = fields.Boolean('Albarán firmado', compute='_compute_firma', store=True)
    signed_by = fields.Char('Firmado por', compute='_compute_firma', store=True)

    _sql_constraints = [('deca_id_uniq', 'UNIQUE(company_id, deca_id)', 'Ese envío de DecaFirma ya está enlazado.')]

    @api.depends('document_ids.state', 'document_ids.version', 'document_ids.url', 'document_ids.sign_url', 'document_ids.signed_at', 'route_deca_url')
    def _compute_enlaces(self):
        for s in self:
            alb = s.vigente('albaran')
            # En una ruta, el DeCA va colgado de la primera parada: las demás enseñan el de la ruta.
            s.deca_url = s.vigente('deca').url or s.route_deca_url
            s.albaran_sign_url = alb.sign_url if alb and not alb.signed_at else False

    @api.depends('document_ids.state', 'document_ids.version', 'document_ids.signed_at', 'document_ids.signed_by')
    def _compute_firma(self):
        for s in self:
            alb = s.vigente('albaran')
            s.signed = bool(alb.signed_at)
            s.signed_by = alb.signed_by

    def vigente(self, kind):
        """El documento vigente de ese tipo, en su última versión."""
        self.ensure_one()
        return self.document_ids.filtered(lambda d: d.kind == kind and d.state == 'vigente').sorted('version', reverse=True)[:1]

    def _origen(self):
        self.ensure_one()
        if not self.res_model or self.res_model not in self.env:
            return None
        rec = self.env[self.res_model].browse(self.res_id).exists()
        return rec or None

    def action_abrir_origen(self):
        self.ensure_one()
        origen = self._origen()
        if not origen:
            raise UserError(_('El registro de origen ya no existe.'))
        return {'type': 'ir.actions.act_window', 'res_model': origen._name, 'res_id': origen.id, 'view_mode': 'form'}

    # ------------------------------------------------------------------
    # Lo que vuelve de DecaFirma
    # ------------------------------------------------------------------

    @staticmethod
    def _fecha(value):
        if not value:
            return False
        return fields.Datetime.to_string(dateparser.isoparse(value).astimezone(timezone.utc).replace(tzinfo=None))

    def _aplicar(self, data, avisar=True):
        """Lleva a Odoo el estado de un envío tal cual lo devuelve DecaFirma (GET /envios/:id)."""
        self.ensure_one()
        if not data or not data.get('id'):
            return
        # Lo que dice DecaFirma lo escribe el módulo, no el usuario: este solo
        # lee los envíos (ver security/ir.access.csv).
        self = self.sudo()
        antes_firmado = {d.deca_id: bool(d.signed_at) for d in self.document_ids}
        antes_version = {d.deca_id: d.version for d in self.document_ids}
        ruta = data.get('ruta') or {}
        vals = {
            'deca_id': data['id'],
            'state': ESTADOS.get(data.get('estado'), 'draft'),
            'error': data.get('error') or False,
            'route_deca_id': ruta.get('id') or False,
            'route_stop': ruta.get('parada') or 0,
        }
        comandos = []
        existentes = {d.deca_id: d for d in self.document_ids}
        for doc in data.get('documentos') or []:
            firmado = doc.get('firmado') or {}
            dv = {
                'deca_id': doc['id'],
                'kind': 'deca' if doc.get('tipo') == 'deca' else 'albaran',
                'reference': doc.get('referencia'),
                'version': doc.get('version') or 1,
                'state': 'anulado' if doc.get('estado') == 'anulado' else 'vigente',
                'void_reason': doc.get('motivoAnulacion') or False,
                'is_demo': bool(doc.get('demo')),
                'url': doc.get('url'),
                'sign_url': doc.get('urlFirma'),
                'pdf_url': doc.get('pdf'),
                'issued_at': self._fecha(doc.get('emitido')),
                'signed_at': self._fecha(firmado.get('cuando')),
                'signed_by': firmado.get('quien'),
                'signed_vat': firmado.get('dni'),
                'signed_remarks': firmado.get('observaciones'),
            }
            if doc['id'] in existentes:
                comandos.append(fields.Command.update(existentes[doc['id']].id, dv))
            else:
                comandos.append(fields.Command.create(dv))
        if comandos:
            vals['document_ids'] = comandos
        nuevos = self._aplicar_papeles(data.get('papeles'), vals)
        self.write(vals)
        if avisar:
            self._contar_cambios(antes_firmado, antes_version)
            for papel in self.papel_ids.filtered(lambda p: p.deca_id in nuevos):
                papel._contar_llegada()

    def _aplicar_papeles(self, papeles, vals):
        """Los papeles del porte que vienen con el envío (solo en los planes que los llevan).

        Devuelve los que no estaban, para contarlos en el chatter con su foto.
        """
        if not papeles:
            return set()
        existentes = {p.deca_id: p for p in self.papel_ids}
        comandos = []
        for papel in papeles:
            if not papel.get('id'):
                continue
            pv = self.env['decafirma.papel']._valores(papel, self._fecha)
            if papel['id'] in existentes:
                comandos.append(fields.Command.update(existentes[papel['id']].id, pv))
            else:
                comandos.append(fields.Command.create(pv))
        if comandos:
            vals['papel_ids'] = comandos
        return {p['id'] for p in papeles if p.get('id')} - set(existentes)

    def _contar_cambios(self, antes_firmado, antes_version):
        """Lo que ha pasado desde la última vez, en el chatter del registro de origen."""
        origen = self._origen()
        if not origen or not hasattr(origen, 'message_post'):
            return
        for d in self.document_ids:
            papel = _('DeCA') if d.kind == 'deca' else _('Albarán')
            if d.signed_at and not antes_firmado.get(d.deca_id):
                self._recibir_firma(origen, d, papel)
            elif d.deca_id not in antes_version:
                origen.message_post(body=_('%(papel)s emitido en DecaFirma: %(ref)s.', papel=papel, ref=d.reference or d.deca_id))
            elif d.version > antes_version[d.deca_id]:
                if d.state == 'anulado':
                    origen.message_post(body=_('%(papel)s %(ref)s anulado: %(motivo)s.', papel=papel, ref=d.reference, motivo=d.void_reason or '—'))
                else:
                    origen.message_post(body=_('%(papel)s %(ref)s, versión %(v)s (mismo QR).', papel=papel, ref=d.reference, v=d.version))

    def _recibir_firma(self, origen, doc, papel):
        """El albarán firmado vuelve a Odoo: el PDF firmado adjunto, quién firmó en el chatter y,
        si firmó con reservas, una actividad para que alguien lo mire."""
        obs = f' — «{doc.signed_remarks}»' if doc.signed_remarks else ''
        adjuntos = []
        try:
            adjuntos = doc._adjuntar().ids
        except (UserError, DecafirmaError) as e:
            # Sin el PDF se cuenta igual; «Albarán» en el registro lo vuelve a pedir.
            _logger.warning('DecaFirma: no se ha podido bajar el PDF firmado de %s: %s', doc.reference, e)
        origen.message_post(
            body=_('%(papel)s %(ref)s firmado por %(quien)s%(obs)s.', papel=papel, ref=doc.reference, quien=doc.signed_by or '?', obs=obs),
            attachment_ids=adjuntos)
        if doc.signed_remarks and hasattr(origen, 'activity_schedule'):
            responsable = getattr(origen, 'user_id', False) or origen.create_uid
            if not responsable or responsable._is_superuser() or not responsable.active:
                responsable = self.env.ref('base.user_admin', raise_if_not_found=False)
            origen.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_('Revisar: firmado con reservas'),
                note=_('%(quien)s firmó %(ref)s con reservas: «%(obs)s».', quien=doc.signed_by or '?', ref=doc.reference, obs=doc.signed_remarks),
                user_id=responsable.id if responsable else self.env.uid)

    def action_actualizar(self):
        comprobar_permiso(self.env)
        for s in self.filtered('deca_id'):
            try:
                _code, data = DecafirmaClient(s.company_id).estado(s.deca_id)
            except DecafirmaError as e:
                raise UserError(str(e)) from e
            s._aplicar(data)
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    # ------------------------------------------------------------------
    # Ponerse al día (por si un aviso no llegó)
    # ------------------------------------------------------------------

    @api.model
    def _cron_poner_al_dia(self, companies=None):
        companies = companies or self.env['res.company'].sudo().search([('decafirma_api_key', '!=', False)])
        for company in companies.sudo():
            desde = company.decafirma_last_sync or (datetime.utcnow() - timedelta(days=2))
            desde_iso = desde.replace(tzinfo=timezone.utc).isoformat()
            try:
                client = DecafirmaClient(company)
                envios = set()
                for _vuelta in range(20):
                    _code, data = client.documentos_desde(desde_iso)
                    envios.update(d['envio'] for d in data.get('documentos') or [])
                    desde_iso = data.get('hasta') or desde_iso
                    if not data.get('hayMas'):
                        break
                envios |= self._papeles_desde(company, client)
                for s in self.sudo().search([('company_id', '=', company.id), ('deca_id', 'in', list(envios))]):
                    _code, estado = client.estado(s.deca_id)
                    s._aplicar(estado)
                company.decafirma_last_sync = self._fecha(desde_iso) or fields.Datetime.now()
            except (DecafirmaError, UserError) as e:
                _logger.warning('DecaFirma: no se ha podido poner al día %s: %s', company.name, e)

    def _papeles_desde(self, company, client):
        """Los envíos con papeles nuevos o cambiados desde la última vez (por si un aviso no llegó).

        Aparte de los documentos, con su propia marca: un papel llega cuando el
        porte ya está firmado y emitido. Si la API aún no lo tiene, no pasa nada.
        """
        desde = company.decafirma_last_sync_papeles or (datetime.utcnow() - timedelta(days=2))
        desde_iso = desde.replace(tzinfo=timezone.utc).isoformat()
        envios = set()
        try:
            for _vuelta in range(20):
                _code, data = client.papeles_desde(desde_iso)
                envios.update(p['envio'] for p in data.get('papeles') or [] if p.get('envio'))
                desde_iso = data.get('hasta') or desde_iso
                if not data.get('hayMas'):
                    break
        except DecafirmaError as e:
            _logger.info('DecaFirma: sin papeles para %s: %s', company.name, e)
            return envios
        company.decafirma_last_sync_papeles = self._fecha(desde_iso) or fields.Datetime.now()
        return envios

    # ------------------------------------------------------------------
    # Asistentes (anular, mandar al conductor)
    # ------------------------------------------------------------------

    def _wizard(self, que, documento):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'decafirma.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_accion': que, 'default_document_id': documento.id},
        }
