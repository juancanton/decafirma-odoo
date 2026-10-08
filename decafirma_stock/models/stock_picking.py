from odoo import _, api, fields, models


class StockPicking(models.Model):
    _name = 'stock.picking'
    _inherit = ['stock.picking', 'decafirma.mixin']

    decafirma_plate = fields.Char('Matrícula', copy=False, help='La del camión que lleva la mercancía. Sale en el DeCA.')
    decafirma_trailer = fields.Char('Remolque', copy=False)
    decafirma_driver_id = fields.Many2one('res.partner', 'Conductor', copy=False,
                                          help='Con su NIF (DNI). Si tiene correo, se le propone al mandarle el documento.')
    decafirma_carrier_id = fields.Many2one('res.partner', 'Transportista', copy=False,
                                           help='Solo si no lo lleváis vosotros: el transportista que hace el porte, con su NIF.')
    decafirma_shipper_id = fields.Many2one('res.partner', 'Cargador', copy=False,
                                           help='Quien contrata el transporte. Vacío: lo de Ajustes > DecaFirma (vosotros o el cliente).')
    decafirma_goods = fields.Char('Mercancía en el DeCA', copy=False,
                                  help='Cómo se describe la carga. Vacío: los productos del albarán.')
    decafirma_visible = fields.Boolean(compute='_compute_decafirma_visible')

    @api.depends('picking_type_code', 'state')
    def _compute_decafirma_visible(self):
        for p in self:
            p.decafirma_visible = p.picking_type_code in ('outgoing', 'internal') and p.state != 'cancel'

    def _decafirma_referencia(self):
        self.ensure_one()
        # Con la base de datos delante: dos bases con el mismo WH/OUT/00012 no chocan en DecaFirma.
        return f'{self.name} ({self.env.cr.dbname}:{self.id})'

    def _decafirma_moves(self):
        return self.move_ids.filtered(lambda m: m.state != 'cancel' and m.product_id)

    def _decafirma_payload(self):
        self.ensure_one()
        company = self.company_id
        cliente = self.partner_id.commercial_partner_id if self.partner_id else self.env['res.partner']
        cargador = self.decafirma_shipper_id or (cliente if company.decafirma_cargador == 'cliente' and cliente else company.partner_id)
        # En Odoo 17 el albarán no tiene warehouse_address_id: la dirección del almacén.
        origen = self.picking_type_id.warehouse_id.partner_id or company.partner_id
        moves = self._decafirma_moves()

        def cantidad(m):
            return m.quantity if self.state == 'done' and m.quantity else m.product_uom_qty

        def kilos(m):
            return round((m.product_id.weight or 0.0) * (m.product_qty or cantidad(m)), 3)

        lineas = [{
            'descripcion': m.product_id.display_name,
            'referencia': m.product_id.default_code or None,
            'bultos': int(cantidad(m)) if float(cantidad(m)).is_integer() else None,
            'pesoKg': kilos(m) or None,
        } for m in moves[:100]]
        # shipping_weight está en stock_delivery en Odoo 17, que no es dependencia.
        peso = ('shipping_weight' in self._fields and self.shipping_weight) or sum(kilos(m) for m in moves)
        productos = moves.mapped('product_id.name')
        mercancia = self.decafirma_goods or (', '.join(productos[:3]) + ('…' if len(productos) > 3 else ''))
        bultos = sum(int(cantidad(m)) for m in moves if float(cantidad(m)).is_integer())
        fecha = self.date_done if self.state == 'done' and self.date_done else self.scheduled_date

        payload = {
            'shipperName': cargador.name,
            'shipperTaxId': cargador.vat,
            'shipperAddress': self._decafirma_domicilio(cargador),
            'consigneeName': cliente.name or (self.partner_id.name if self.partner_id else None),
            'consigneeTaxId': cliente.vat,
            'consigneeEmail': cliente.email,
            'consigneeContact': self.partner_id.name if self.partner_id and self.partner_id != cliente else None,
            'goodsDescription': mercancia or None,
            'weightKg': round(peso, 2) if peso else None,
            'packages': bultos or None,
            'plate': self.decafirma_plate,
            'trailerPlate': self.decafirma_trailer,
            'driverName': self.decafirma_driver_id.name,
            'driverTaxId': self.decafirma_driver_id.vat,
            'driverPhone': self.decafirma_driver_id.phone,
            'lineas': lineas,
        }
        if self.decafirma_carrier_id:
            c = self.decafirma_carrier_id.commercial_partner_id
            payload.update({'carrierName': c.name, 'carrierTaxId': c.vat, 'carrierPhone': c.phone, 'carrierEmail': c.email})
        payload.update(self._decafirma_direccion(origen, 'origin'))
        payload.update(self._decafirma_direccion(self.partner_id, 'destination'))
        if fecha:
            local = fields.Datetime.context_timestamp(self, fecha)
            payload['transportDate'] = local.strftime('%Y-%m-%d')
            payload['transportTime'] = local.strftime('%H:%M')
        return payload

    def _decafirma_pdf_odoo(self):
        """El albarán de entrega de Odoo de siempre (Inventario > Imprimir > Albarán de entrega)."""
        self.ensure_one()
        pdf, _tipo = self.env['ir.actions.report'].sudo()._render_qweb_pdf('stock.action_report_delivery', self.ids)
        return pdf

    def _action_done(self):
        res = super()._action_done()
        # Al validar, si la empresa lo tiene puesto. Si no se puede, el albarán
        # se valida igual: el motivo queda en su chatter para arreglarlo.
        auto = self.filtered(lambda p: p.picking_type_code == 'outgoing' and p.company_id.decafirma_auto != 'no'
                             and p.company_id.sudo().decafirma_api_key)
        for picking in auto:
            avisos = picking._decafirma_emitir(picking.company_id.decafirma_auto)
            if avisos.get(picking):
                picking.message_post(body=_('DecaFirma no ha emitido al validar: %s', avisos[picking]))
        return res
