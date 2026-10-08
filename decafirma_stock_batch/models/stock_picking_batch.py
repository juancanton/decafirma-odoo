from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.decafirma.models.client import MAX_PDFS_RUTA, DecafirmaClient, DecafirmaError
from odoo.addons.decafirma.models.decafirma_mixin import comprobar_permiso


class StockPickingBatch(models.Model):
    """Una agrupación de albaranes de salida = una ruta de reparto en DecaFirma.

    Un solo DeCA para todas las paradas (mismo cargador y mismo transportista,
    Resolución de 5 de junio de 2026, apartado sexto) y un albarán por parada,
    que firma quien recibe en cada una. Cada albarán de Odoo es una parada y
    lleva su propio envío de DecaFirma (con su albarán y su firma); el DeCA es
    el de la ruta.
    """
    _inherit = 'stock.picking.batch'

    decafirma_route_id = fields.Char('Ruta en DecaFirma', copy=False, readonly=True, index=True)
    decafirma_deca_url = fields.Char('QR del DeCA de la ruta', copy=False, readonly=True)
    decafirma_deca_ref = fields.Char('DeCA de la ruta', copy=False, readonly=True)
    decafirma_deca_version = fields.Integer('Versión del DeCA', copy=False, readonly=True)
    decafirma_error = fields.Char('Aviso de DecaFirma', copy=False, readonly=True)
    decafirma_plate = fields.Char('Matrícula', copy=False)
    decafirma_trailer = fields.Char('Remolque', copy=False)
    decafirma_driver_id = fields.Many2one('res.partner', 'Conductor', copy=False)
    decafirma_albaranes = fields.Boolean('Un albarán por parada', default=True,
                                        help='Cada parada con su albarán, que firma quien recibe en su móvil.')
    decafirma_visible = fields.Boolean(compute='_compute_decafirma_visible')
    decafirma_pendientes = fields.Integer('Paradas sin llevar a DecaFirma', compute='_compute_decafirma_visible')

    @api.depends('picking_type_code', 'state', 'picking_ids', 'picking_ids.decafirma_shipment_id', 'decafirma_route_id')
    def _compute_decafirma_visible(self):
        for b in self:
            b.decafirma_visible = b.picking_type_code == 'outgoing' and b.state != 'cancel'
            b.decafirma_pendientes = len(b._decafirma_paradas_nuevas()) if b.decafirma_route_id else 0

    # ------------------------------------------------------------------
    # Lo que se manda
    # ------------------------------------------------------------------

    def _decafirma_pickings(self):
        return self.picking_ids.filtered(lambda p: p.state != 'cancel').sorted(lambda p: (p.scheduled_date or fields.Datetime.now(), p.id))

    def _decafirma_paradas_nuevas(self):
        """Albaranes de la agrupación que aún no van en la ruta."""
        self.ensure_one()
        return self._decafirma_pickings().filtered(
            lambda p: not p.decafirma_shipment_id or p.decafirma_shipment_id.route_deca_id != self.decafirma_route_id)

    @staticmethod
    def _parada(picking):
        """Una parada de POST /api/v1/rutas a partir de su albarán (lo mismo que su DeCA suelto)."""
        x = picking._decafirma_payload()
        parada = {
            'tipo': 'entrega' if picking.picking_type_code == 'outgoing' else 'recogida',
            'lugar': {'nombre': x.get('destinationName'), 'direccion': x.get('destinationAddress'), 'cp': x.get('destinationPostcode'),
                      'ciudad': x.get('destinationCity'), 'provincia': x.get('destinationProvince')},
            'destinatario': x.get('consigneeName'),
            'destinatarioNif': x.get('consigneeTaxId'),
            'contacto': x.get('consigneeContact'),
            'telefono': x.get('destinationPhone'),
            'correo': x.get('consigneeEmail'),
            'mercancia': x.get('goodsDescription'),
            'bultos': x.get('packages'),
            'pesoKg': x.get('weightKg'),
            'numeroAlbaran': picking._decafirma_numero_albaran(),
        }
        parada['lugar'] = {k: v for k, v in parada['lugar'].items() if v}
        return {k: v for k, v in parada.items() if v not in (None, False, '', {})}

    def _decafirma_comun(self, pickings):
        self.ensure_one()
        primero = pickings[:1]
        x = primero._decafirma_payload()
        shipper = {k: x.get(k) for k in ('shipperName', 'shipperTaxId', 'shipperAddress', 'carrierName', 'carrierTaxId', 'carrierPhone', 'carrierEmail')}
        # Una ruta va en un DeCA solo si todas las paradas tienen el mismo cargador.
        otros = {p._decafirma_payload().get('shipperTaxId') for p in pickings} - {shipper.get('shipperTaxId')}
        if otros:
            raise UserError(_('Una ruta lleva un solo cargador, y estos albaranes tienen varios. Pon el cargador en Ajustes > DecaFirma '
                              '(«Nuestra empresa») o en cada albarán.'))
        fecha = fields.Datetime.context_timestamp(self, self.scheduled_date or fields.Datetime.now())
        base = self.picking_type_id.warehouse_id.partner_id or self.company_id.partner_id
        comun = dict(shipper,
                     transportDate=fecha.strftime('%Y-%m-%d'), transportTime=fecha.strftime('%H:%M'),
                     plate=self.decafirma_plate, trailerPlate=self.decafirma_trailer,
                     driverName=self.decafirma_driver_id.name, driverTaxId=self.decafirma_driver_id.vat,
                     driverPhone=self.decafirma_driver_id.phone,
                     base={'nombre': base.name, 'direccion': ', '.join(filter(None, [base.street, base.street2])) or None,
                           'cp': base.zip, 'ciudad': base.city, 'provincia': base.state_id.name})
        comun['base'] = {k: v for k, v in comun['base'].items() if v}
        return primero._decafirma_limpio(comun) | {'base': comun['base']}

    # ------------------------------------------------------------------
    # Lo que vuelve
    # ------------------------------------------------------------------

    def _decafirma_aplicar_ruta(self, data, pickings_nuevos=None):
        """Lleva a Odoo la ruta (GET /rutas/:id): el DeCA a la agrupación y cada parada a su albarán."""
        self.ensure_one()
        deca = data.get('deca') or {}
        self.write({
            'decafirma_route_id': data.get('id'),
            'decafirma_deca_url': deca.get('url'),
            'decafirma_deca_ref': deca.get('referencia'),
            'decafirma_deca_version': deca.get('version') or 0,
            'decafirma_error': False,
        })
        conocidos = {p.decafirma_shipment_id.deca_id: p for p in self.picking_ids if p.decafirma_shipment_id.deca_id}
        sin_envio = list(pickings_nuevos or [])
        for parada in data.get('paradas') or []:
            picking = conocidos.get(parada['id'])
            if not picking:
                if not sin_envio:
                    continue
                picking = sin_envio.pop(0)
            envio = picking._decafirma_envio()
            envio.sudo().route_deca_url = deca.get('url')
            envio._aplicar(parada)

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------

    def _decafirma_aviso(self, mensaje, tipo='success'):
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('DecaFirma'), 'message': mensaje, 'type': tipo, 'sticky': tipo != 'success',
            'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}

    def action_decafirma_emitir_ruta(self):
        """Crea la ruta con un DeCA para todas las paradas o, si ya existe, le añade las nuevas."""
        comprobar_permiso(self.env)
        self.ensure_one()
        client = DecafirmaClient(self.company_id)
        nuevas = self._decafirma_paradas_nuevas()
        if not nuevas:
            raise UserError(_('Todas las paradas van ya en la ruta.'))
        if any(p.decafirma_shipment_id.deca_id for p in nuevas):
            raise UserError(_('%s ya tiene su DeCA suelto: anúlalo antes de meterlo en la ruta.',
                              ', '.join(nuevas.filtered(lambda p: p.decafirma_shipment_id.deca_id).mapped('name'))))
        paradas = [self._parada(p) for p in nuevas]
        if self.decafirma_albaranes:
            # «Firmar el albarán de Odoo» también en la ruta: cada parada, su PDF (si no hay, el de DecaFirma).
            # Entre todos, lo que acepta DecaFirma en una llamada: los que no caben, con el de DecaFirma.
            total = 0
            for picking, parada in zip(nuevas, paradas):
                pdf = picking._decafirma_pdf_propio()
                if not pdf:
                    continue
                if total + len(pdf) > MAX_PDFS_RUTA:
                    picking._decafirma_nota(_('Los albaranes de Odoo de esta ruta pasan de 40 MB entre todos: '
                                              'en esta parada se firma el de DecaFirma.'))
                    continue
                total += len(pdf)
                parada['albaranPdf'] = DecafirmaClient.pdf_base64(pdf)
        try:
            if not self.decafirma_route_id:
                pickings = self._decafirma_pickings()
                payload = {'referencia': f'{self.name} ({self.env.cr.dbname}:{self.id})', 'comun': self._decafirma_comun(pickings),
                           'paradas': paradas, 'albaranes': self.decafirma_albaranes}
                code, data = client.crear_ruta(payload)
            else:
                code, data = client.anadir_paradas(self.decafirma_route_id, {'paradas': paradas, 'albaranes': self.decafirma_albaranes})
        except DecafirmaError as e:
            self.decafirma_error = str(e)
            return self._decafirma_aviso(str(e), 'warning')
        self._decafirma_aplicar_ruta(data, nuevas)
        return self._decafirma_aviso(_('Ruta en DecaFirma: %(ref)s, versión %(v)s, %(n)s paradas.',
                                       ref=self.decafirma_deca_ref, v=self.decafirma_deca_version, n=len(data.get('paradas') or [])))

    def action_decafirma_quitar_paradas(self):
        """Saca de la ruta las paradas cuyo albarán ya no está en la agrupación (el DeCA sale en versión nueva)."""
        comprobar_permiso(self.env)
        self.ensure_one()
        if not self.decafirma_route_id:
            return
        fuera = self.env['decafirma.shipment'].search([('route_deca_id', '=', self.decafirma_route_id), ('company_id', '=', self.company_id.id)])
        fuera = fuera.filtered(lambda s: s.res_model == 'stock.picking' and s.res_id not in self.picking_ids.ids)
        client = DecafirmaClient(self.company_id)
        avisos = []
        for envio in fuera:
            try:
                code, data = client.quitar_parada(self.decafirma_route_id, envio.deca_id)
            except DecafirmaError as e:
                avisos.append(f'{envio.name}: {e}')
                continue
            if code == 422:
                avisos.append(f"{envio.name}: {data.get('error')}")
                continue
            envio.sudo().write({'route_deca_id': False, 'route_deca_url': False})
            envio.action_actualizar()
            if data.get('id'):
                self._decafirma_aplicar_ruta(data)
        if avisos:
            return self._decafirma_aviso('\n'.join(avisos), 'warning')
        return self._decafirma_aviso(_('Quitadas de la ruta: %s.', ', '.join(fuera.mapped('name')) or '—'))

    def action_decafirma_actualizar(self):
        comprobar_permiso(self.env)
        self.ensure_one()
        if not self.decafirma_route_id:
            return
        try:
            _code, data = DecafirmaClient(self.company_id).ruta(self.decafirma_route_id)
        except DecafirmaError as e:
            raise UserError(str(e)) from e
        self._decafirma_aplicar_ruta(data)
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    def action_decafirma_ver_deca(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'url': self.decafirma_deca_url, 'target': 'new'}
