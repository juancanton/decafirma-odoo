import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .client import MAX_PDF_PROPIO, DecafirmaClient, DecafirmaError

_logger = logging.getLogger(__name__)

GRUPO_USUARIO = 'decafirma.group_decafirma_user'
GRUPO_RESPONSABLE = 'decafirma.group_decafirma_manager'


def comprobar_permiso(env, grupo=GRUPO_USUARIO):
    """Como en DecaFirma: quien emite (Usuario) y quien además anula y lleva los ajustes (Responsable).

    En el servidor y no solo en los botones: un botón escondido se puede llamar igual.
    """
    if env.su or env.user.has_group(grupo):
        return
    raise AccessError(_('Para esto hace falta el permiso «DecaFirma / %s». Pídeselo a quien administra Odoo.',
                        _('Responsable') if grupo == GRUPO_RESPONSABLE else _('Usuario')))


class DecafirmaMixin(models.AbstractModel):
    """Lo que hereda cualquier cosa que emite en DecaFirma (un albarán de Inventario, un servicio del TMS…).

    Quien lo hereda dice qué se manda en `_decafirma_payload` (los campos de
    POST /api/v1/envios, los mismos que el formulario de DecaFirma) y, si quiere,
    la referencia en `_decafirma_referencia`. Lo demás (emitir, corregir,
    anular, mandar al conductor, ponerse al día) va aquí.
    """
    _name = 'decafirma.mixin'
    _description = 'Emite en DecaFirma'

    decafirma_shipment_id = fields.Many2one('decafirma.shipment', 'Envío en DecaFirma', copy=False, readonly=True, index=True)
    # Sin seguimiento: lo que pasa ya se cuenta en el chatter con su mensaje (`_contar_cambios`).
    decafirma_state = fields.Selection(related='decafirma_shipment_id.state', string='DecaFirma', tracking=False)
    decafirma_error = fields.Char(related='decafirma_shipment_id.error', string='Aviso de DecaFirma')
    decafirma_document_ids = fields.One2many(related='decafirma_shipment_id.document_ids', string='Documentos de DecaFirma')
    decafirma_papel_ids = fields.One2many(related='decafirma_shipment_id.papel_ids', string='Papeles del porte')
    decafirma_deca_url = fields.Char(related='decafirma_shipment_id.deca_url', string='QR del DeCA')
    decafirma_signed = fields.Boolean(related='decafirma_shipment_id.signed', string='Albarán firmado')
    decafirma_has_deca = fields.Boolean(compute='_compute_decafirma_has')
    decafirma_has_albaran = fields.Boolean(compute='_compute_decafirma_has')
    decafirma_signed_by = fields.Char(related='decafirma_shipment_id.signed_by', string='Firmado por')

    @api.depends('decafirma_shipment_id.document_ids.state')
    def _compute_decafirma_has(self):
        for rec in self:
            s = rec.decafirma_shipment_id
            rec.decafirma_has_deca = bool(s and s.vigente('deca'))
            rec.decafirma_has_albaran = bool(s and s.vigente('albaran'))

    # ------------------------------------------------------------------
    # Lo que tiene que dar quien hereda
    # ------------------------------------------------------------------

    def _decafirma_payload(self):
        """Los campos del envío para POST /api/v1/envios (sin «emitir» ni «referencia»)."""
        raise NotImplementedError

    def _decafirma_referencia(self):
        self.ensure_one()
        return f'{self._name}:{self.id}:{self.display_name}'

    def _decafirma_numero_albaran(self):
        """El número del albarán si la empresa usa el de Odoo; si no, None (numeración de DecaFirma).

        Si se firma el albarán de Odoo, siempre su número: es ese papel.
        """
        self.ensure_one()
        c = self.company_id
        return self.display_name if c.decafirma_numero_odoo or c.decafirma_pdf_odoo else None

    def _decafirma_pdf_odoo(self):
        """El PDF del albarán que hace Odoo, para firmar ese («Firmar el albarán de Odoo»), o None.

        Lo da quien hereda (en Inventario, el albarán de entrega de siempre).
        """
        return None

    def _decafirma_pdf_propio(self):
        """El PDF de Odoo si la empresa firma el suyo y se puede sacar; si no, None y sale el de DecaFirma."""
        self.ensure_one()
        if not self.company_id.decafirma_pdf_odoo:
            return None
        try:
            pdf = self._decafirma_pdf_odoo()
        except Exception as e:  # noqa: BLE001 — sin el PDF de Odoo se emite igual, con el de DecaFirma
            _logger.warning('DecaFirma: no se ha podido sacar el PDF de %s: %s', self.display_name, e)
            return None
        # En las pruebas de Odoo los informes salen en HTML: eso no es un PDF y DecaFirma no lo aceptaría.
        if not pdf or pdf[:5] != b'%PDF-':
            return None
        if len(pdf) > MAX_PDF_PROPIO:
            # DecaFirma no lo aceptaría y no saldría nada: mejor el suyo, y que se sepa por qué.
            self._decafirma_nota(_('El albarán de Odoo pesa más de 5 MB: se firma el de DecaFirma.'))
            return None
        return pdf

    def _decafirma_nota(self, mensaje):
        """Una nota en el chatter de lo que origina el envío, si lo tiene."""
        if hasattr(self, 'message_post'):
            self.message_post(body=mensaje)

    # ------------------------------------------------------------------
    # Ayudas
    # ------------------------------------------------------------------

    @staticmethod
    def _decafirma_limpio(payload):
        """Sin vacíos y con los NIF españoles sin el «ES» de VIES que pone Odoo: en el DeCA va el NIF tal cual."""
        out = {}
        for k, v in payload.items():
            if v in (None, False, '') or v == []:
                continue
            if k.endswith('TaxId') and isinstance(v, str):
                v = v.replace(' ', '').replace('-', '')
                if v.upper().startswith('ES') and len(v) == 11:
                    v = v[2:]
            out[k] = v
        return out

    @staticmethod
    def _decafirma_domicilio(partner):
        """«Calle 1, 08202 Sabadell, Barcelona»: hecho a mano, igual en todas las versiones de Odoo
        (_display_address cambia de argumentos entre la 19 y la 20)."""
        if not partner:
            return None
        poblacion = ' '.join(filter(None, [partner.zip, partner.city]))
        return ', '.join(filter(None, [partner.street, partner.street2, poblacion, partner.state_id.name])) or None

    @staticmethod
    def _decafirma_direccion(partner, prefijo):
        """El sitio de carga o de descarga a partir de un contacto de Odoo."""
        if not partner:
            return {}
        return {
            f'{prefijo}Name': partner.name,
            f'{prefijo}Address': ', '.join(filter(None, [partner.street, partner.street2])) or None,
            f'{prefijo}Postcode': partner.zip,
            f'{prefijo}City': partner.city,
            f'{prefijo}Province': partner.state_id.name,
            f'{prefijo}Phone': partner.phone,
        }

    def _decafirma_envio(self):
        """El registro del envío en Odoo; se crea la primera vez."""
        self.ensure_one()
        if not self.decafirma_shipment_id:
            self.decafirma_shipment_id = self.env['decafirma.shipment'].sudo().create({
                # Lo que ve el usuario; la referencia para DecaFirma (con la base) va aparte.
                'name': self.display_name,
                'company_id': self.company_id.id,
                'res_model': self._name,
                'res_id': self.id,
            })
        return self.decafirma_shipment_id

    # ------------------------------------------------------------------
    # Emitir, corregir, actualizar
    # ------------------------------------------------------------------

    def _decafirma_emitir(self, tipo):
        """Emite 'deca', 'albaran' o 'ambos'. Devuelve {registro: aviso} con lo que no se ha podido."""
        avisos = {}
        tipos = ['deca', 'albaran'] if tipo == 'ambos' else [tipo]
        for rec in self:
            envio = rec._decafirma_envio()
            faltan = [t for t in tipos if not envio.vigente(t)]
            if not faltan:
                continue
            try:
                client = DecafirmaClient(rec.company_id)
                numero = rec._decafirma_numero_albaran() if 'albaran' in faltan else None
                pdf = rec._decafirma_pdf_propio() if 'albaran' in faltan else None
                if not envio.deca_id:
                    payload = dict(rec._decafirma_limpio(rec._decafirma_payload()), referencia=rec._decafirma_referencia(),
                                   emitir='ambos' if len(faltan) == 2 else faltan[0])
                    if numero:
                        payload['numeroAlbaran'] = numero
                    if pdf:
                        payload['albaranPdf'] = client.pdf_base64(pdf)
                    _code, data = client.crear_envio(payload)
                    envio._aplicar(data)
                    faltan = [t for t in tipos if not envio.vigente(t)] if data.get('repetido') else []
                    if data.get('error'):
                        avisos[rec] = data['error']
                        continue
                for t in faltan:
                    _code, data = client.emitir(envio.deca_id, t, numero=numero if t == 'albaran' else None,
                                                pdf=pdf if t == 'albaran' else None)
                    envio._aplicar(data)
                    if data.get('error'):
                        avisos[rec] = data['error']
                        break
            except (DecafirmaError, UserError) as e:
                envio.sudo().error = str(e)
                avisos[rec] = str(e)
        return avisos

    def _decafirma_corregir(self):
        """Lleva a DecaFirma lo que ha cambiado en Odoo: sale la versión nueva con el mismo QR.

        Devuelve (avisos, nuevas): lo que no se ha podido y los documentos que han salido en versión nueva.
        """
        avisos, nuevas = {}, []
        for rec in self.filtered(lambda r: r.decafirma_shipment_id.deca_id):
            envio = rec.decafirma_shipment_id
            try:
                _code, data = DecafirmaClient(rec.company_id).corregir(envio.deca_id, rec._decafirma_limpio(rec._decafirma_payload()))
            except (DecafirmaError, UserError) as e:
                avisos[rec] = str(e)
                continue
            envio._aplicar(data)
            if data.get('error'):
                avisos[rec] = data['error']
            nuevas += data.get('nuevasVersiones') or []
        return avisos, nuevas

    def _decafirma_notificar(self, avisos, ok):
        if avisos:
            msg = '\n'.join(f'{r.display_name}: {m}' for r, m in avisos.items())
            return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
                'title': _('DecaFirma'), 'message': msg, 'type': 'warning', 'sticky': True,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('DecaFirma'), 'message': ok, 'type': 'success',
            'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}

    def action_decafirma_emitir_deca(self):
        comprobar_permiso(self.env)
        return self._decafirma_notificar(self._decafirma_emitir('deca'), _('DeCA emitido.'))

    def action_decafirma_emitir_albaran(self):
        comprobar_permiso(self.env)
        return self._decafirma_notificar(self._decafirma_emitir('albaran'), _('Albarán emitido.'))

    def action_decafirma_emitir_ambos(self):
        comprobar_permiso(self.env)
        return self._decafirma_notificar(self._decafirma_emitir('ambos'), _('DeCA y albarán emitidos.'))

    def action_decafirma_corregir(self):
        comprobar_permiso(self.env)
        avisos, nuevas = self._decafirma_corregir()
        ok = (_('Versión nueva de %s, con el mismo QR.', ', '.join(nuevas)) if nuevas
              else _('No había nada que llevar: DecaFirma ya lo tenía así.'))
        return self._decafirma_notificar(avisos, ok)

    def action_decafirma_actualizar(self):
        self.mapped('decafirma_shipment_id').action_actualizar()
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    def action_decafirma_ver_deca(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'url': self.decafirma_deca_url, 'target': 'new'}

    def action_decafirma_ver_albaran(self):
        """Firmado, el PDF firmado (adjunto al registro); sin firmar, el del QR."""
        self.ensure_one()
        alb = self.decafirma_shipment_id.vigente('albaran')
        if not alb:
            raise UserError(_('Emite primero el albarán.'))
        if alb.signed_at:
            adjunto = alb._adjunto_al_dia() or alb.sudo()._adjuntar()
            return {'type': 'ir.actions.act_url', 'url': f'/web/content/{adjunto.id}', 'target': 'new'}
        return {'type': 'ir.actions.act_url', 'url': alb.url, 'target': 'new'}

    def action_decafirma_enviar(self):
        comprobar_permiso(self.env)
        self.ensure_one()
        doc = self.decafirma_shipment_id.vigente('deca') or self.decafirma_shipment_id.vigente('albaran')
        if not doc:
            raise UserError(_('Emite primero el DeCA o el albarán.'))
        return self.decafirma_shipment_id._wizard('enviar', doc)
