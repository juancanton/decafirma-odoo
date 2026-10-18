import hashlib
import hmac
import json
from unittest.mock import patch

import base64

from odoo.exceptions import AccessError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.decafirma.models.client import DecafirmaClient


def _envio(id_='env1', docs=(), estado='documented', ruta=None):
    return {'id': id_, 'status': estado, 'route': ruta, 'documents': list(docs)}


def _doc(id_, tipo, ref, version=1, estado='active', firmado=None):
    """Un documento como lo manda la API: tipo 'deca' o 'albaran' (en la API, 'delivery_note')."""
    tipo_api = 'delivery_note' if tipo == 'albaran' else tipo
    d = {'id': id_, 'type': tipo_api, 'reference': ref, 'version': version, 'status': estado, 'isDemo': False,
         'publicUrl': f'https://decafirma.test/d/{id_}', 'pdfUrl': f'https://decafirma.test/api/v1/documents/{id_}/pdf',
         'issuedAt': '2026-10-07T10:00:00Z'}
    if tipo == 'albaran':
        d['signUrl'] = f'https://decafirma.test/d/{id_}/firmar'
        d['signature'] = firmado
    return d


def _ruta(docs_paradas, deca_id='d1'):
    """Una ruta como la manda la API (POST /routes): el DeCA de la ruta y sus paradas."""
    return {'id': 'r1', 'deca': {'id': deca_id, 'reference': 'DECA-R', 'version': 1, 'status': 'active',
                                 'publicUrl': f'https://decafirma.test/d/{deca_id}'},
            'stops': docs_paradas}


class DecafirmaComun(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.sudo().write({'decafirma_api_key': 'dfk_prueba', 'decafirma_url': 'https://decafirma.test',
                                  'vat': 'ESB12345674', 'decafirma_auto': 'no'})
        cls.cliente = cls.env['res.partner'].create({'name': 'Ferretería Central', 'is_company': True, 'city': 'Granollers', 'zip': '08401'})
        cls.conductor = cls.env['res.partner'].create({'name': 'Antonio Ruiz', 'vat': '12345678Z', 'email': 'antonio@test.es'})
        almacenable = {'is_storable': True} if 'is_storable' in cls.env['product.product']._fields else {'type': 'product'}
        cls.producto = cls.env['product.product'].create({'name': 'Saco 25 kg', 'default_code': 'S25', 'weight': 25.0, **almacenable})
        cls.wh = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)

    def _albaran(self, qty=40, cliente=None):
        p = self.env['stock.picking'].create({
            'picking_type_id': self.wh.out_type_id.id, 'partner_id': (cliente or self.cliente).id,
            'location_id': self.wh.lot_stock_id.id, 'location_dest_id': self.env.ref('stock.stock_location_customers').id,
            'move_ids': [(0, 0, {**({'name': self.producto.name} if 'name' in self.env['stock.move']._fields else {}),
                                 'product_id': self.producto.id, 'product_uom_qty': qty, 'location_id': self.wh.lot_stock_id.id,
                                 'location_dest_id': self.env.ref('stock.stock_location_customers').id})],
            'decafirma_plate': '4821KLM', 'decafirma_driver_id': self.conductor.id,
        })
        p.action_confirm()
        return p


@tagged('post_install', '-at_install')
class TestDecafirmaStock(DecafirmaComun):

    def test_payload(self):
        p = self._albaran()
        x = p._decafirma_limpio(p._decafirma_payload())
        self.assertEqual(x['shipperTaxId'], 'B12345674', 'el NIF sin el «ES» de VIES')
        self.assertEqual(x['consigneeName'], 'Ferretería Central')
        self.assertEqual(x['weightKg'], 1000.0, 'el peso de los productos')
        self.assertEqual(x['packages'], 40)
        self.assertEqual(x['plate'], '4821KLM')
        self.assertEqual(x['driverTaxId'], '12345678Z')
        self.assertEqual(x['lines'][0]['reference'], 'S25')
        self.assertEqual(x['destinationCity'], 'Granollers')

    def test_emitir_y_firma_por_aviso(self):
        p = self._albaran()
        llamadas = []

        def falso(cliente, metodo, ruta, payload=None, params=None):
            llamadas.append((metodo, ruta, payload))
            return 201, _envio(docs=[_doc('d1', 'deca', 'DECA-1'), _doc('a1', 'albaran', 'ALB-1')])

        with patch.object(DecafirmaClient, '_request', falso):
            p.action_decafirma_emitir_ambos()
        self.assertEqual(llamadas[0][0:2], ('POST', '/shipments'))
        self.assertEqual(llamadas[0][2]['issue'], 'both')
        self.assertIn(p.name, llamadas[0][2]['reference'], 'la referencia lleva el albarán (y la base): idempotente')
        self.assertEqual(p.decafirma_state, 'documented')
        self.assertTrue(p.decafirma_has_deca and p.decafirma_has_albaran)
        self.assertEqual(p.decafirma_deca_url, 'https://decafirma.test/d/d1')

        # Lo que trae un aviso de firma: el PDF firmado vuelve adjunto
        with patch.object(DecafirmaClient, 'pdf', lambda cliente, url: b'%PDF-1.7 firmado'):
            p.decafirma_shipment_id._aplicar(_envio(docs=[
                _doc('d1', 'deca', 'DECA-1'),
                _doc('a1', 'albaran', 'ALB-1', version=2, firmado={'signedAt': '2026-10-07T11:00:00Z', 'signerName': 'Jordi Martí', 'signerTaxId': None, 'remarks': 'Un saco roto'}),
            ]))
        self.assertTrue(p.decafirma_signed)
        mensaje = p.message_ids[0]
        self.assertIn('firmado por Jordi Martí', mensaje.body)
        self.assertIn('Un saco roto', mensaje.body)
        self.assertEqual(mensaje.attachment_ids.mapped('name'), ['Albaran ALB-1 firmado v2.pdf'], 'el PDF firmado, en el chatter')
        self.assertEqual(bytes(mensaje.attachment_ids.raw), b'%PDF-1.7 firmado')
        actividad = p.activity_ids.filtered(lambda a: 'reservas' in (a.summary or ''))
        self.assertEqual(len(actividad), 1, 'firmado con reservas: una actividad para revisarlo')
        self.assertEqual(actividad.summary, 'Revisar: firmado con reservas')
        # «Albarán · Firmado» abre el firmado, sin volver a bajarlo
        with patch.object(DecafirmaClient, 'pdf', side_effect=AssertionError('ya lo tenía')):
            r = p.action_decafirma_ver_albaran()
        self.assertEqual(r['url'], f'/web/content/{mensaje.attachment_ids.id}')

    def test_firma_sin_reservas_no_pide_revisar(self):
        p = self._albaran()
        with patch.object(DecafirmaClient, '_request', lambda *a, **k: (201, _envio(docs=[_doc('a1', 'albaran', 'ALB-1')]))):
            p.action_decafirma_emitir_albaran()
        with patch.object(DecafirmaClient, 'pdf', lambda cliente, url: b'%PDF-1.7'):
            p.decafirma_shipment_id._aplicar(_envio(docs=[_doc('a1', 'albaran', 'ALB-1', 2, firmado={'signedAt': '2026-10-07T11:00:00Z', 'signerName': 'Ana'})]))
        self.assertFalse(p.activity_ids.filtered(lambda a: 'reservas' in (a.summary or '')))

    def test_firmar_el_albaran_de_odoo(self):
        self.company.decafirma_pdf_odoo = True
        p = self._albaran()
        enviado = {}

        def falso(cliente, metodo, ruta, payload=None, params=None):
            enviado.update(payload)
            return 201, _envio(docs=[_doc('a1', 'albaran', p.name)])

        pdf_odoo = b'%PDF-1.7 el albaran de Odoo'
        with patch.object(type(p), '_decafirma_pdf_odoo', lambda rec: pdf_odoo), patch.object(DecafirmaClient, '_request', falso):
            p.action_decafirma_emitir_albaran()
        self.assertEqual(base64.b64decode(enviado['deliveryNotePdf']), pdf_odoo, 'se manda el PDF de Odoo')
        self.assertEqual(enviado['deliveryNoteNumber'], p.name, 'con su número, aunque no esté puesto el de Odoo')
        self.assertEqual(enviado['issue'], 'delivery_note')

    def test_sin_pdf_de_verdad_sale_el_de_decafirma(self):
        # En las pruebas, Odoo saca los informes en HTML: eso no se manda.
        self.company.decafirma_pdf_odoo = True
        p = self._albaran()
        enviado = {}

        def falso(cliente, metodo, ruta, payload=None, params=None):
            enviado.update(payload)
            return 201, _envio(docs=[_doc('a1', 'albaran', p.name)])

        with patch.object(DecafirmaClient, '_request', falso):
            p.action_decafirma_emitir_albaran()
        self.assertNotIn('deliveryNotePdf', enviado)
        self.assertEqual(p.decafirma_state, 'documented')

    def test_sin_firmar_pdf_odoo_no_se_manda(self):
        p = self._albaran()
        enviado = {}

        def falso(cliente, metodo, ruta, payload=None, params=None):
            enviado.update(payload)
            return 201, _envio(docs=[_doc('a1', 'albaran', 'ALB-1')])

        with patch.object(type(p), '_decafirma_pdf_odoo', side_effect=AssertionError('no tocaba')), patch.object(DecafirmaClient, '_request', falso):
            p.action_decafirma_emitir_albaran()
        self.assertNotIn('deliveryNotePdf', enviado)

    def test_no_emite_dos_veces(self):
        p = self._albaran()
        with patch.object(DecafirmaClient, '_request', lambda *a, **k: (201, _envio(docs=[_doc('d1', 'deca', 'DECA-1')]))):
            p.action_decafirma_emitir_deca()
        with patch.object(DecafirmaClient, '_request', side_effect=AssertionError('no tenía que llamar')):
            p.action_decafirma_emitir_deca()

    def test_error_de_emision_se_dice(self):
        p = self._albaran()
        with patch.object(DecafirmaClient, '_request', lambda *a, **k: (422, dict(_envio(estado='draft'), error={'code': 'unprocessable', 'message': 'Falta la matrícula'}))):
            r = p.action_decafirma_emitir_deca()
        self.assertEqual(r['params']['type'], 'warning')
        self.assertIn('Falta la matrícula', r['params']['message'])
        self.assertEqual(p.decafirma_error, 'Falta la matrícula')

    def test_error_de_la_api_se_ensena(self):
        # La API contesta {"error": {"code", "message", "details"}}: se enseña el mensaje (y los detalles).
        class Respuesta:
            status_code = 400
            reason = 'Bad Request'

            @staticmethod
            def json():
                return {'error': {'code': 'validation_error', 'message': 'Faltan datos.', 'details': ['Falta la matrícula.']}}

        p = self._albaran()
        with patch('odoo.addons.decafirma.models.client.requests.request', lambda *a, **k: Respuesta()):
            r = p.action_decafirma_emitir_deca()
        self.assertEqual(r['params']['type'], 'warning')
        self.assertIn('Datos no válidos para DecaFirma. Falta la matrícula. (400)', r['params']['message'])

    def test_reintento_lleva_lo_corregido(self):
        # Laura (15-oct): sin matrícula se crea el envío y no se emite; al ponerla y volver a emitir,
        # la matrícula tiene que llegar a DecaFirma antes de emitir ese mismo envío.
        p = self._albaran()
        p.decafirma_plate = False
        with patch.object(DecafirmaClient, '_request', lambda *a, **k: (422, dict(_envio(estado='draft'), error={'code': 'unprocessable', 'message': 'Sin esto no se emite: Matrícula del vehículo'}))):
            p.action_decafirma_emitir_deca()
        self.assertEqual(p.decafirma_shipment_id.deca_id, 'env1')
        p.decafirma_plate = '1234ABC'
        llamadas = []

        def falso(cliente, metodo, ruta, payload=None, params=None):
            llamadas.append((metodo, ruta, payload))
            if metodo == 'PATCH':
                return 200, _envio(estado='draft')
            return 201, _envio(docs=[_doc('d1', 'deca', 'DECA-1')])
        with patch.object(DecafirmaClient, '_request', falso):
            r = p.action_decafirma_emitir_deca()
        self.assertEqual([(m, ruta) for m, ruta, _ in llamadas], [('PATCH', '/shipments/env1'), ('POST', '/shipments/env1/issue')])
        self.assertEqual(llamadas[0][2]['plate'], '1234ABC')
        self.assertEqual(llamadas[1][2], {'type': 'deca'})
        self.assertEqual(r['params']['type'], 'success')

    def test_ya_emitido_no_se_corrige_al_emitir_lo_otro(self):
        # Con el DeCA ya sacado, emitir el albarán no reescribe el envío: eso es «Corregir», que saca versión nueva.
        p = self._albaran()
        with patch.object(DecafirmaClient, '_request', lambda *a, **k: (201, _envio(docs=[_doc('d1', 'deca', 'DECA-1')]))):
            p.action_decafirma_emitir_deca()
        llamadas = []

        def falso(cliente, metodo, ruta, payload=None, params=None):
            llamadas.append((metodo, ruta))
            return 201, _envio(docs=[_doc('d1', 'deca', 'DECA-1'), _doc('a1', 'albaran', 'ALB-1')])
        with patch.object(DecafirmaClient, '_request', falso):
            p.action_decafirma_emitir_albaran()
        self.assertEqual(llamadas, [('POST', '/shipments/env1/issue')])

    def test_ruta_desde_agrupacion(self):
        ps = self._albaran(10) | self._albaran(20)
        batch = self.env['stock.picking.batch'].create({'picking_ids': [(6, 0, ps.ids)], 'decafirma_plate': '4821KLM',
                                                        'decafirma_driver_id': self.conductor.id})
        enviado = {}

        def falso(cliente, metodo, ruta, payload=None, params=None):
            enviado['payload'] = payload
            return 201, _ruta([_envio('e1', [_doc('d1', 'deca', 'DECA-R'), _doc('a1', 'albaran', 'ALB-1')], ruta={'id': 'r1', 'stop': 1}),
                               _envio('e2', [_doc('a2', 'albaran', 'ALB-2')], ruta={'id': 'r1', 'stop': 2})])

        with patch.object(DecafirmaClient, '_request', falso):
            batch.action_decafirma_emitir_ruta()
        self.assertEqual(len(enviado['payload']['stops']), 2)
        self.assertEqual(enviado['payload']['stops'][0]['type'], 'delivery')
        self.assertEqual(enviado['payload']['stops'][0]['location']['city'], 'Granollers')
        self.assertEqual(enviado['payload']['common']['plate'], '4821KLM')
        self.assertTrue(enviado['payload']['deliveryNotes'])
        self.assertEqual(batch.decafirma_deca_ref, 'DECA-R')
        self.assertEqual(batch.decafirma_route_id, 'r1')
        self.assertEqual(ps.mapped('decafirma_shipment_id.route_stop'), [1, 2])
        self.assertTrue(all(p.decafirma_deca_url == 'https://decafirma.test/d/d1' for p in ps), 'las dos paradas enseñan el DeCA de la ruta')

    def test_ruta_firma_el_albaran_de_odoo(self):
        self.company.decafirma_pdf_odoo = True
        ps = self._albaran(10) | self._albaran(20)
        batch = self.env['stock.picking.batch'].create({'picking_ids': [(6, 0, ps.ids)], 'decafirma_plate': '4821KLM',
                                                        'decafirma_driver_id': self.conductor.id, 'decafirma_albaranes': True})
        enviado = {}

        def falso(cliente, metodo, ruta, payload=None, params=None):
            enviado['payload'] = payload
            return 201, _ruta([_envio('e1', [_doc('a1', 'albaran', ps[0].name)], ruta={'id': 'r1', 'stop': 1}),
                               _envio('e2', [_doc('a2', 'albaran', ps[1].name)], ruta={'id': 'r1', 'stop': 2})])

        # El primero tiene su PDF; el segundo no se puede sacar (en las pruebas sale en HTML): lleva el de DecaFirma.
        pdfs = {ps[0].id: b'%PDF-1.7 el albaran de Odoo', ps[1].id: b'<html>'}
        with patch.object(type(ps), '_decafirma_pdf_odoo', lambda rec: pdfs[rec.id]), patch.object(DecafirmaClient, '_request', falso):
            batch.action_decafirma_emitir_ruta()
        uno, dos = enviado['payload']['stops']
        self.assertEqual(base64.b64decode(uno['deliveryNotePdf']), pdfs[ps[0].id], 'la parada con PDF manda el albarán de Odoo')
        self.assertEqual(uno['deliveryNoteNumber'], ps[0].name, 'con su número')
        self.assertNotIn('deliveryNotePdf', dos, 'la que no tiene PDF de verdad, sin él')

    def test_ruta_sin_albaranes_no_saca_pdf(self):
        self.company.decafirma_pdf_odoo = True
        ps = self._albaran(10) | self._albaran(20)
        batch = self.env['stock.picking.batch'].create({'picking_ids': [(6, 0, ps.ids)], 'decafirma_plate': '4821KLM',
                                                        'decafirma_driver_id': self.conductor.id, 'decafirma_albaranes': False})
        enviado = {}

        def falso(cliente, metodo, ruta, payload=None, params=None):
            enviado['payload'] = payload
            return 201, _ruta([_envio('e1', [], ruta={'id': 'r1', 'stop': 1}), _envio('e2', [], ruta={'id': 'r1', 'stop': 2})])

        with patch.object(type(ps), '_decafirma_pdf_odoo', side_effect=AssertionError('no tocaba')), patch.object(DecafirmaClient, '_request', falso):
            batch.action_decafirma_emitir_ruta()
        self.assertFalse(any('deliveryNotePdf' in p for p in enviado['payload']['stops']), 'sin albaranes en la ruta, no se manda ningún PDF')

    def test_pdf_demasiado_grande_sale_el_de_decafirma(self):
        self.company.decafirma_pdf_odoo = True
        p = self._albaran()
        enviado = {}

        def falso(cliente, metodo, ruta, payload=None, params=None):
            enviado.update(payload)
            return 201, _envio(docs=[_doc('a1', 'albaran', p.name)])

        # Más de lo que acepta DecaFirma (5 MB; aquí, 10 bytes): se emite igual, con el suyo, y queda dicho.
        with patch('odoo.addons.decafirma.models.decafirma_mixin.MAX_PDF_PROPIO', 10), \
                patch.object(type(p), '_decafirma_pdf_odoo', lambda rec: b'%PDF-1.7 el albaran de Odoo'), \
                patch.object(DecafirmaClient, '_request', falso):
            p.action_decafirma_emitir_albaran()
        self.assertNotIn('deliveryNotePdf', enviado)
        self.assertTrue(p.decafirma_shipment_id.document_ids, 'el albarán sale igual')
        self.assertIn('pesa más de 5 MB', ' '.join(p.message_ids.mapped('body')))

    def test_ruta_con_pdfs_de_mas_los_ultimos_con_el_de_decafirma(self):
        self.company.decafirma_pdf_odoo = True
        ps = self._albaran(10) | self._albaran(20) | self._albaran(30)
        batch = self.env['stock.picking.batch'].create({'picking_ids': [(6, 0, ps.ids)], 'decafirma_plate': '4821KLM',
                                                        'decafirma_driver_id': self.conductor.id, 'decafirma_albaranes': True})
        enviado = {}

        def falso(cliente, metodo, ruta, payload=None, params=None):
            enviado['payload'] = payload
            return 201, _ruta([_envio(f'e{i}', [], ruta={'id': 'r1', 'stop': i}) for i in (1, 2, 3)])

        # Entre todos, como mucho 40 MB (aquí, 60 bytes): caben los dos primeros y el tercero lleva el de DecaFirma.
        pdf = b'%PDF-1.7 ' + b'x' * 21
        with patch('odoo.addons.decafirma_stock.models.stock_picking_batch.MAX_PDFS_RUTA', 60), \
                patch.object(type(ps), '_decafirma_pdf_odoo', lambda rec: pdf), patch.object(DecafirmaClient, '_request', falso):
            batch.action_decafirma_emitir_ruta()
        self.assertEqual(['deliveryNotePdf' in p for p in enviado['payload']['stops']], [True, True, False])
        self.assertEqual(batch.decafirma_route_id, 'r1', 'la ruta sale igual')
        self.assertIn('pasan de 40 MB', ' '.join(ps[2].message_ids.mapped('body')))


def _papel(id_='pp1', numero='T-0042', peso='24380.00', facturado=None):
    return {'id': id_, 'type': 'weighbridge_ticket', 'number': numero, 'issuer': 'Áridos del Vallès', 'date': '2026-10-08',
            'netWeightKg': peso, 'parsed': True, 'mimeType': 'image/jpeg', 'uploadedBy': 'el conductor',
            'uploadedAt': '2026-10-08T09:30:00Z', 'invoicedAt': facturado, 'fileUrl': f'https://decafirma.test/api/v1/attachments/{id_}/file'}


@tagged('post_install', '-at_install')
class TestDecafirmaPapeles(DecafirmaComun):
    """Los papeles del porte (la foto de la carta de porte o del ticket de báscula) llegan al albarán."""

    def _emitido(self):
        p = self._albaran()
        with patch.object(DecafirmaClient, '_request', lambda *a, **k: (201, _envio(docs=[_doc('d1', 'deca', 'DECA-1')]))):
            p.action_decafirma_emitir_deca()
        return p

    def test_papel_llega_con_su_foto(self):
        p = self._emitido()
        pedidas = []

        def archivo(cliente, url):
            pedidas.append(url)
            return b'\xff\xd8\xff foto'

        envio = dict(_envio(docs=[_doc('d1', 'deca', 'DECA-1')]), attachments=[_papel()])
        with patch.object(DecafirmaClient, 'archivo', archivo):
            p.decafirma_shipment_id._aplicar(envio)
            p.decafirma_shipment_id._aplicar(envio)  # el mismo aviso dos veces: un solo papel y una sola nota
        papel = p.decafirma_papel_ids
        self.assertEqual(len(papel), 1)
        self.assertEqual((papel.numero, papel.peso_neto_kg, papel.facturado), ('T-0042', 24380.0, False))
        self.assertEqual(pedidas, ['https://decafirma.test/api/v1/attachments/pp1/file'], 'la foto se baja una vez')
        self.assertEqual(papel.attachment_id.res_id, p.id, 'la foto, adjunta al albarán')
        notas = p.message_ids.filtered(lambda m: 'Papel del porte' in (m.body or ''))
        self.assertEqual(len(notas), 1)
        self.assertIn('24.380,00 kg netos', notas.body)
        self.assertIn('(ticket de báscula)', notas.body)
        self.assertEqual(papel.attachment_id.name, 'Ticket de báscula T-0042.jpg')
        self.assertEqual(notas.attachment_ids, papel.attachment_id)

    def test_sin_papeles_no_cambia_nada(self):
        p = self._emitido()
        p.decafirma_shipment_id._aplicar(_envio(docs=[_doc('d1', 'deca', 'DECA-1')]))
        self.assertFalse(p.decafirma_papel_ids, 'un plan sin papeles no trae «attachments» (o viene vacío)')

    def test_marcar_facturado(self):
        p = self._emitido()
        with patch.object(DecafirmaClient, 'archivo', lambda c, u: b'foto'):
            p.decafirma_shipment_id._aplicar(dict(_envio(docs=[_doc('d1', 'deca', 'DECA-1')]), attachments=[_papel()]))
        llamadas = []

        def falso(cliente, metodo, ruta, payload=None, params=None):
            llamadas.append((metodo, ruta, payload))
            return 200, _papel(facturado='2026-10-08T12:00:00Z' if payload['invoiced'] else None)

        with patch.object(DecafirmaClient, '_request', falso):
            p.decafirma_papel_ids.action_marcar_facturado()
            p.decafirma_papel_ids.action_marcar_facturado()  # ya lo está: no vuelve a llamar
        self.assertEqual(llamadas, [('POST', '/attachments/pp1/invoiced', {'invoiced': True})])
        self.assertTrue(p.decafirma_papel_ids.facturado)
        with patch.object(DecafirmaClient, '_request', falso):
            p.decafirma_papel_ids.action_marcar_pendiente()
        self.assertFalse(p.decafirma_papel_ids.facturado, 'y se puede deshacer')

    def test_ponerse_al_dia_trae_los_papeles(self):
        p = self._emitido()
        envio_id = p.decafirma_shipment_id.deca_id

        def falso(cliente, metodo, ruta, payload=None, params=None):
            if ruta == '/documents':
                return 200, {'data': [], 'until': '2026-10-08T10:00:00Z', 'hasMore': False}
            if ruta == '/attachments':
                return 200, {'data': [dict(_papel(), shipmentId=envio_id)], 'until': '2026-10-08T10:00:00Z', 'hasMore': False}
            return 200, dict(_envio(docs=[_doc('d1', 'deca', 'DECA-1')]), attachments=[_papel()])

        with patch.object(DecafirmaClient, '_request', falso), patch.object(DecafirmaClient, 'archivo', lambda c, u: b'foto'):
            self.env['decafirma.shipment']._cron_poner_al_dia(self.company)
        self.assertEqual(p.decafirma_papel_ids.numero, 'T-0042', 'un aviso perdido se recupera en la tarea de cada 15 minutos')
        self.assertTrue(self.company.decafirma_last_sync_papeles)

    def test_sin_la_api_de_papeles_se_pone_al_dia_igual(self):
        p = self._emitido()
        from odoo.addons.decafirma.models.client import DecafirmaError

        def falso(cliente, metodo, ruta, payload=None, params=None):
            if ruta == '/attachments':
                raise DecafirmaError('DecaFirma: No existe (404)')
            if ruta == '/documents':
                return 200, {'data': [{'shipmentId': p.decafirma_shipment_id.deca_id}], 'until': '2026-10-08T10:00:00Z', 'hasMore': False}
            return 200, _envio(docs=[_doc('d1', 'deca', 'DECA-1', version=2)])

        with patch.object(DecafirmaClient, '_request', falso):
            self.env['decafirma.shipment']._cron_poner_al_dia(self.company)
        self.assertEqual(p.decafirma_shipment_id.vigente('deca').version, 2, 'los documentos se ponen al día aunque la API no tenga papeles')


@tagged('post_install', '-at_install')
class TestDecafirmaPermisos(DecafirmaComun):

    def _usuario(self, login, *grupos):
        campo = 'group_ids' if 'group_ids' in self.env['res.users']._fields else 'groups_id'
        ids = [self.env.ref('base.group_user').id, self.env.ref('stock.group_stock_user').id] + [self.env.ref(g).id for g in grupos]
        return self.env['res.users'].create({'name': f'María {login}', 'login': login, campo: [(6, 0, ids)]})

    def test_sin_permiso_no_emite(self):
        p = self._albaran().with_user(self._usuario('sin'))
        with patch.object(DecafirmaClient, '_request', side_effect=AssertionError('no tenía que llamar')):
            with self.assertRaises(AccessError):
                p.action_decafirma_emitir_deca()

    def test_usuario_emite_y_no_anula(self):
        maria = self._usuario('usuaria', 'decafirma.group_decafirma_user')
        p = self._albaran()
        cabeceras = {}

        def falso(cliente, metodo, ruta, payload=None, params=None):
            cabeceras.update(cliente._headers())
            return 201, _envio(docs=[_doc('d1', 'deca', 'DECA-1')])

        with patch.object(DecafirmaClient, '_request', falso):
            p.with_user(maria).action_decafirma_emitir_deca()
        self.assertEqual(cabeceras['X-DecaFirma-Client'], 'Odoo')
        self.assertEqual(cabeceras['X-DecaFirma-User'], 'Mar%C3%ADa usuaria', 'quién emite, para «Emitido por María (desde Odoo)»')
        doc = p.decafirma_shipment_id.vigente('deca')
        with self.assertRaises(AccessError):
            doc.with_user(maria).action_anular()
        asistente = self.env['decafirma.wizard'].with_user(maria).create({'accion': 'anular', 'document_id': doc.id, 'motivo': 'x'})
        with self.assertRaises(AccessError):
            asistente.action_confirmar()

    def test_responsable_anula(self):
        jefa = self._usuario('jefa', 'decafirma.group_decafirma_manager')
        p = self._albaran()
        with patch.object(DecafirmaClient, '_request', lambda *a, **k: (201, _envio(docs=[_doc('d1', 'deca', 'DECA-1')]))):
            p.action_decafirma_emitir_deca()
        doc = p.decafirma_shipment_id.vigente('deca')
        with patch.object(DecafirmaClient, '_request', lambda *a, **k: (200, _envio(docs=[_doc('d1', 'deca', 'DECA-1', 2, 'voided')]))):
            self.env['decafirma.wizard'].with_user(jefa).create({'accion': 'anular', 'document_id': doc.id, 'motivo': 'No sale'}).action_confirmar()
        self.assertEqual(doc.state, 'anulado')

    def test_el_robot_no_se_presenta(self):
        self.assertNotIn('X-DecaFirma-User', DecafirmaClient(self.company.with_user(self.env.ref('base.user_root')))._headers())


@tagged('post_install', '-at_install')
class TestDecafirmaWebhook(HttpCase):

    def test_firma_del_aviso(self):
        company = self.env.company
        company.sudo().decafirma_webhook_secret = 'whsec_prueba'
        envio = self.env['decafirma.shipment'].create({'name': 'X', 'res_model': 'res.partner', 'res_id': self.env.user.partner_id.id,
                                                       'deca_id': 'envW', 'company_id': company.id})
        cuerpo = json.dumps({'type': 'document.voided', 'createdAt': '2026-10-17T10:00:00Z',
                             'document': {'id': 'dW', 'type': 'deca', 'reference': 'DECA-W', 'version': 2, 'status': 'voided'},
                             'routeId': None, 'shipment': _envio('envW', [_doc('dW', 'deca', 'DECA-W', 2, 'voided')])}).encode()
        url = f'/decafirma/webhook/{company.id}'
        cabeceras = {'Content-Type': 'application/json', 'X-DecaFirma-Event': 'document.voided', 'X-DecaFirma-Delivery': 'dl_1'}
        mala = self.url_open(url, data=cuerpo, headers=dict(cabeceras, **{'X-DecaFirma-Signature': 'sha256=0'}))
        self.assertEqual(mala.status_code, 401, 'sin la firma buena no se toca nada')
        firma = 'sha256=' + hmac.new(b'whsec_prueba', cuerpo, hashlib.sha256).hexdigest()
        buena = self.url_open(url, data=cuerpo, headers=dict(cabeceras, **{'X-DecaFirma-Signature': firma}))
        self.assertEqual(buena.status_code, 200)
        envio.invalidate_recordset()
        self.assertEqual(envio.document_ids.state, 'anulado')

    def test_aviso_de_prueba(self):
        company = self.env.company
        company.sudo().decafirma_webhook_secret = 'whsec_prueba'
        cuerpo = json.dumps({'type': 'test', 'createdAt': '2026-10-17T10:00:00Z', 'message': 'Hola desde DecaFirma'}).encode()
        firma = 'sha256=' + hmac.new(b'whsec_prueba', cuerpo, hashlib.sha256).hexdigest()
        r = self.url_open(f'/decafirma/webhook/{company.id}', data=cuerpo, headers={
            'Content-Type': 'application/json', 'X-DecaFirma-Event': 'test', 'X-DecaFirma-Signature': firma})
        self.assertEqual(r.status_code, 200)


@tagged('post_install', '-at_install')
class TestDecafirmaMotivos(TransactionCase):
    """Los errores de la API llegan en inglés y se enseñan en castellano por su «reason»."""

    def test_motivos_en_castellano(self):
        from odoo.addons.decafirma.models.client import mensaje_de_error
        falta = {'error': {'code': 'unprocessable', 'message': 'The DeCA was not issued: Missing required fields: plate.',
                           'details': {'reason': 'missing_fields', 'fields': ['plate'], 'document': 'deca'}}}
        self.assertEqual(mensaje_de_error(falta), 'No se ha emitido el DeCA: Faltan datos para emitir: Matrícula del vehículo.')
        repetido = {'error': {'code': 'unprocessable', 'message': 'x',
                              'details': {'reason': 'duplicate_delivery_note_number', 'number': 'A/7'}}}
        self.assertEqual(mensaje_de_error(repetido), 'Ya tienes un documento con el número A/7. Pon otro.')
        # Un motivo que este módulo aún no conoce: el mensaje en inglés, tal cual.
        nuevo = {'error': {'code': 'unprocessable', 'message': 'Something new.', 'details': {'reason': 'brand_new'}}}
        self.assertEqual(mensaje_de_error(nuevo), 'Something new.')
        self.assertIn('Ajustes > DecaFirma', mensaje_de_error({'error': {'code': 'invalid_api_key', 'message': 'x'}}))
        self.assertIsNone(mensaje_de_error({'id': 'x'}))


class SyncFalso:
    """Un DecaFirma de mentira para POST /sync: guarda lo mandado (salvo con dryRun) y contesta como el de verdad."""

    def __init__(self, malos=()):
        self.llamadas, self.guardado, self.malos = [], {}, set(malos)

    def __call__(self, metodo, ruta, payload=None, params=None, timeout=None):
        # patch.object con un objeto (no una función): no recibe el cliente.
        assert (metodo, ruta) == ('POST', '/sync'), (metodo, ruta)
        self.llamadas.append(json.loads(json.dumps(payload)))
        r = {'dryRun': payload.get('dryRun')}
        if 'company' in payload:
            antes = self.guardado.get('company') or {}
            r['company'] = {'updated': sorted(k for k, v in payload['company'].items() if antes.get(k) != v)}
        if 'settings' in payload:
            antes = self.guardado.get('settings') or {}
            r['settings'] = {'updated': sorted(k for k, v in payload['settings'].items() if antes.get(k) != v)}
        for tipo in ('series', 'customers', 'places', 'people', 'vehicles'):
            if tipo not in payload:
                continue
            g = self.guardado.setdefault(tipo, {})
            t = r[tipo] = {'created': 0, 'updated': 0, 'unchanged': 0, 'archived': 0, 'skipped': [], 'ids': {}}
            for x in payload[tipo]:
                e = x['externalId']
                if x.get('taxId') in self.malos:
                    t['skipped'].append({'externalId': e, 'reason': 'invalid_tax_id', 'field': 'taxId',
                                         'value': x['taxId'], 'message': 'Invalid tax ID.'})
                    continue
                antes = g.get(e)
                clave = 'unchanged' if antes == x else ('archived' if x.get('archived') else ('updated' if antes else 'created'))
                t[clave] += 1
                t['ids'][e] = 'dc_' + e.replace(':', '_')
                if not payload.get('dryRun'):
                    g[e] = x
        if not payload.get('dryRun'):
            for k in ('company', 'settings'):
                if k in payload:
                    self.guardado[k] = payload[k]
        return 200, r


@tagged('post_install', '-at_install')
class TestDecafirmaSync(DecafirmaComun):
    """La puesta a punto: lo de Odoo, a DecaFirma."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company.partner_id.write({'street': 'Carrer Major 1', 'zip': '08202', 'city': 'Sabadell', 'phone': '937000000'})
        Partner = cls.env['res.partner']
        cls.con_nif = Partner.create({'name': 'Hierros del Vallès SL', 'is_company': True, 'vat': 'ESB66778899',
                                      'city': 'Terrassa', 'zip': '08221', 'street': 'Av. Industria 5'})
        cls.sin_nif = Partner.create({'name': 'Bar Paco', 'is_company': True, 'city': 'Rubí'})
        cls.entrega = Partner.create({'name': 'Nave 2', 'type': 'delivery', 'parent_id': cls.con_nif.id,
                                      'street': 'Polígono Sur 3', 'city': 'Terrassa', 'zip': '08223'})
        cls.cliente.vat = 'A08001851'
        # Que sean clientes: se les ha entregado algo.
        for p in (cls.con_nif, cls.sin_nif, cls.cliente):
            cls.env['stock.picking'].create({
                'picking_type_id': cls.wh.out_type_id.id, 'partner_id': p.id, 'location_id': cls.wh.lot_stock_id.id,
                'location_dest_id': cls.env.ref('stock.stock_location_customers').id})

    def _wizard(self, **vals):
        return self.env['decafirma.sync.wizard'].create(dict({'company_id': self.company.id}, **vals))

    def test_ver_y_mandar(self):
        falso = SyncFalso()
        w = self._wizard()
        # Con los datos de demostración (17) hay más clientes: se cuenta lo que hay.
        datos = w._sync_datos()
        n = len(datos['customers'])
        self.assertIn('Bar Paco', [nombre for nombre, _m in datos['fuera']['customers']], 'sin NIF')
        self.assertIn(f'{n} con NIF', w.cuanto_clientes)
        self.assertIn(f"({len(datos['fuera']['customers'])} no se mandan)", w.cuanto_clientes)
        with patch.object(DecafirmaClient, '_request', falso):
            w.action_ver()
            self.assertEqual(w.paso, 'ver')
            self.assertTrue(falso.llamadas[0]['dryRun'], 'primero, ver sin guardar nada')
            self.assertFalse(self.env['decafirma.sync.link'].search([]), 'ver no guarda nada en Odoo')
            w.action_mandar()
        self.assertEqual(w.paso, 'hecho')
        cuerpo = falso.llamadas[1]
        self.assertFalse(cuerpo['dryRun'])
        clientes = {c['externalId']: c for c in cuerpo['customers']}
        self.assertEqual(clientes[f'odoo:res.partner:{self.con_nif.id}']['taxId'], 'B66778899', 'el NIF sin el ES')
        self.assertEqual(clientes[f'odoo:res.partner:{self.con_nif.id}']['city'], 'Terrassa')
        self.assertIn(f'odoo:res.partner:{self.cliente.id}', clientes)
        self.assertNotIn(f'odoo:res.partner:{self.sin_nif.id}', clientes, 'sin NIF no se manda')
        self.assertNotIn(f'odoo:res.partner:{self.company.partner_id.id}', clientes, 'la propia empresa no es cliente')
        lugares = {p['externalId']: p for p in cuerpo['places']}
        self.assertEqual(lugares[f'odoo:res.partner:{self.entrega.id}']['name'], 'Hierros del Vallès SL - Nave 2')
        self.assertIn(f'odoo:stock.warehouse:{self.wh.id}', lugares, 'el almacén, como lugar')
        self.assertEqual(cuerpo['company']['legalName'], self.company.name)
        self.assertEqual(cuerpo['company']['taxId'], 'B12345674')
        self.assertEqual(cuerpo['company']['city'], 'Sabadell')
        self.assertEqual(cuerpo['settings'], {'manualDeliveryNoteNumber': False})
        self.assertFalse([p for p in cuerpo.get('people', []) if p['kind'] == 'office'], 'la gente de oficina, solo si se marca')
        self.assertNotIn('series', cuerpo, 'las series, solo si se marcan')
        self.assertIn('Bar Paco', w.resumen)
        self.assertIn('Sin NIF', w.resumen)
        self.assertIn('cambiado: razón social', w.resumen, 'los datos de la empresa: manda Odoo')
        link = self.env['decafirma.sync.link'].search([('external_id', '=', f'odoo:res.partner:{self.con_nif.id}')])
        self.assertEqual(link.deca_id, f'dc_odoo_res.partner_{self.con_nif.id}')
        self.assertIn('Puesta a punto de DecaFirma', self.company.partner_id.message_ids[0].body)

        # Repetir: todo sin cambios y nada se toca en Odoo.
        escrito = {lk.id: lk.write_date for lk in self.env['decafirma.sync.link'].search([])}
        fecha_cliente = self.con_nif.write_date
        w2 = self._wizard()
        with patch.object(DecafirmaClient, '_request', falso):
            w2.action_mandar()
        r = falso.llamadas[-1]
        self.assertEqual(len(r['customers']), n)
        self.assertEqual({lk.id: lk.write_date for lk in self.env['decafirma.sync.link'].search([])}, escrito)
        self.assertEqual(self.con_nif.write_date, fecha_cliente)
        self.assertIn(f'<td>0</td><td>0</td><td>{n}</td>', w2.resumen.replace(' ', ''), 'todo sin cambios')
        self.assertIn('Datos de la empresa</td><td colspan="5">sin cambios', w2.resumen)

        # Cambia el teléfono en Odoo: solo eso cambia en DecaFirma.
        self.company.partner_id.phone = '938888888'
        w4 = self._wizard(con_clientes=False, con_lugares=False)
        with patch.object(DecafirmaClient, '_request', falso):
            w4.action_ver()
        self.assertIn('cambia: teléfono', w4.resumen)

        # Se archiva un cliente en Odoo: se archiva en DecaFirma.
        self.cliente.active = False
        w3 = self._wizard()
        with patch.object(DecafirmaClient, '_request', falso):
            w3.action_mandar()
        archivado = [c for c in falso.llamadas[-1]['customers'] if c['externalId'] == f'odoo:res.partner:{self.cliente.id}']
        self.assertTrue(archivado and archivado[0]['archived'], 'archivado en Odoo, archivado en DecaFirma')

    def test_lo_que_se_vacia_en_odoo_se_vacia_en_decafirma(self):
        falso = SyncFalso()
        self.con_nif.phone = '931234567'
        with patch.object(DecafirmaClient, '_request', falso):
            self._wizard(con_lugares=False).action_mandar()
            self.con_nif.phone = False
            self._wizard(con_lugares=False).action_mandar()
        ext = f'odoo:res.partner:{self.con_nif.id}'
        item = [c for c in falso.llamadas[-1]['customers'] if c['externalId'] == ext][0]
        self.assertIsNone(item['phone'], 'el teléfono que vino de Odoo y ya no está: null')
        self.assertNotIn('email', item, 'lo que nunca vino de Odoo no se toca')

    def test_formato_del_albaran(self):
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.company.decafirma_formato_albaran = 'ALB%%-%%'
        self.company.decafirma_formato_albaran = 'alb[yy]%%%%%'
        falso = SyncFalso()
        w = self._wizard(con_clientes=False, con_lugares=False, con_empresa=False)
        with patch.object(DecafirmaClient, '_request', falso):
            w.action_ver()
        self.assertEqual(falso.llamadas[0]['settings'], {'manualDeliveryNoteNumber': False, 'deliveryNoteFormat': 'ALB[YY]%%%%%'})
        self.assertIn('formato del número del albarán', w.resumen)

    def test_nif_malo_se_dice_en_castellano(self):
        falso = SyncFalso(malos={'B66778899'})
        w = self._wizard(con_lugares=False, con_empresa=False, con_ajustes=False)
        with patch.object(DecafirmaClient, '_request', falso):
            w.action_ver()
        self.assertIn('Hierros del Vallès SL', w.resumen)
        self.assertIn('El NIF (B66778899) no es válido', w.resumen)

    def test_series_y_oficina(self):
        falso = SyncFalso()
        self.env.ref('base.user_admin').email = 'admin@test.es'
        self.wh.out_type_id.sequence_id.write({'prefix': 'ALB/%(year)s/', 'padding': 5, 'suffix': False})
        w = self._wizard(con_series=True, con_oficina=True, invitar=True, con_clientes=False, con_lugares=False)
        with patch.object(DecafirmaClient, '_request', falso):
            w.action_ver()
        cuerpo = falso.llamadas[0]
        serie = [s for s in cuerpo['series'] if s['externalId'] == f'odoo:stock.picking.type:{self.wh.out_type_id.id}']
        self.assertEqual(serie[0]['format'], 'ALB/[YYYY]/%%%%%')
        self.assertEqual(serie[0]['nextNumber'], self.wh.out_type_id.sequence_id.number_next_actual, 'con su contador')
        admin = [p for p in cuerpo['people'] if p['externalId'] == f'odoo:res.users:{self.env.ref("base.user_admin").id}']
        self.assertEqual((admin[0]['kind'], admin[0]['role']), ('office', 'admin'))
        self.assertEqual(admin[0].get('invite'), bool(admin[0].get('email')), 'invitar, solo con correo')

    def test_por_tandas(self):
        """Más de 500: varias llamadas, y el resumen las suma."""
        falso = SyncFalso()
        self.env['res.partner'].create([{'name': f'Cliente {i}', 'is_company': True, 'vat': 'B66778899',
                                         'customer_rank': 1} if 'customer_rank' in self.env['res.partner']._fields
                                        else {'name': f'Cliente {i}', 'is_company': True, 'vat': 'B66778899'}
                                        for i in range(600)])
        w = self._wizard(con_lugares=False)
        datos = w._sync_datos()
        if len(datos['customers']) <= 500:
            self.skipTest('sin Facturación, los clientes salen de los albaranes')
        with patch.object(DecafirmaClient, '_request', falso):
            w.action_ver()
        self.assertGreaterEqual(len(falso.llamadas), 2)
        self.assertTrue(all(len(c.get('customers', [])) <= 500 for c in falso.llamadas))
        self.assertIn('company', falso.llamadas[0])
        self.assertNotIn('company', falso.llamadas[1])

    def test_sin_permiso_no_manda(self):
        usuario = self.env['res.users'].create({'name': 'Emisor', 'login': 'emisor_sync',
                                                'group_ids' if 'group_ids' in self.env['res.users']._fields else 'groups_id':
                                                [(6, 0, [self.env.ref('decafirma.group_decafirma_user').id])]})
        with self.assertRaises(AccessError):
            self._wizard().with_user(usuario).action_ver()

    def test_motivos_de_la_puesta_a_punto(self):
        from odoo.addons.decafirma.models.motivos import omitido
        self.assertEqual(omitido({'reason': 'missing_fields', 'fields': ['name', 'city']}), 'Falta: Nombre, Población.')
        self.assertEqual(omitido({'reason': 'plate_taken', 'plate': '1234KLM'}), 'La matrícula 1234KLM ya es de otro vehículo de DecaFirma.')
        self.assertIn('Conductor habitual', omitido({'reason': 'unknown_reference', 'field': 'driverExternalId', 'value': 'x'}))
        self.assertEqual(omitido({'reason': 'nuevo', 'message': 'Brand new.'}), 'Brand new.')
