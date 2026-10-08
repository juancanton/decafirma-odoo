import base64
from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.decafirma.models.client import DecafirmaClient
from odoo.addons.decafirma_stock.tests.test_decafirma_stock import DecafirmaComun, _doc, _envio


@tagged('post_install', '-at_install')
class TestDecafirmaStockBatch(DecafirmaComun):

    def test_ruta_desde_agrupacion(self):
        ps = self._albaran(10) | self._albaran(20)
        batch = self.env['stock.picking.batch'].create({'picking_ids': [(6, 0, ps.ids)], 'decafirma_plate': '4821KLM',
                                                        'decafirma_driver_id': self.conductor.id})
        enviado = {}

        def falso(cliente, metodo, ruta, payload=None, params=None):
            enviado['payload'] = payload
            return 201, {'id': 'r1', 'deca': {'id': 'd1', 'referencia': 'DECA-R', 'version': 1, 'url': 'https://decafirma.test/d/d1'},
                         'paradas': [_envio('e1', [_doc('d1', 'deca', 'DECA-R'), _doc('a1', 'albaran', 'ALB-1')], ruta={'id': 'r1', 'parada': 1}),
                                     _envio('e2', [_doc('a2', 'albaran', 'ALB-2')], ruta={'id': 'r1', 'parada': 2})]}

        with patch.object(DecafirmaClient, '_request', falso):
            batch.action_decafirma_emitir_ruta()
        self.assertEqual(len(enviado['payload']['paradas']), 2)
        self.assertEqual(enviado['payload']['comun']['plate'], '4821KLM')
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
            return 201, {'id': 'r1', 'deca': {'id': 'd1', 'referencia': 'DECA-R', 'version': 1, 'url': 'https://decafirma.test/d/d1'},
                         'paradas': [_envio('e1', [_doc('a1', 'albaran', ps[0].name)], ruta={'id': 'r1', 'parada': 1}),
                                     _envio('e2', [_doc('a2', 'albaran', ps[1].name)], ruta={'id': 'r1', 'parada': 2})]}

        # El primero tiene su PDF; el segundo no se puede sacar (en las pruebas sale en HTML): lleva el de DecaFirma.
        pdfs = {ps[0].id: b'%PDF-1.7 el albaran de Odoo', ps[1].id: b'<html>'}
        with patch.object(type(ps), '_decafirma_pdf_odoo', lambda rec: pdfs[rec.id]), patch.object(DecafirmaClient, '_request', falso):
            batch.action_decafirma_emitir_ruta()
        uno, dos = enviado['payload']['paradas']
        self.assertEqual(base64.b64decode(uno['albaranPdf']), pdfs[ps[0].id], 'la parada con PDF manda el albarán de Odoo')
        self.assertEqual(uno['numeroAlbaran'], ps[0].name, 'con su número')
        self.assertNotIn('albaranPdf', dos, 'la que no tiene PDF de verdad, sin él')

    def test_ruta_sin_albaranes_no_saca_pdf(self):
        self.company.decafirma_pdf_odoo = True
        ps = self._albaran(10) | self._albaran(20)
        batch = self.env['stock.picking.batch'].create({'picking_ids': [(6, 0, ps.ids)], 'decafirma_plate': '4821KLM',
                                                        'decafirma_driver_id': self.conductor.id, 'decafirma_albaranes': False})
        enviado = {}

        def falso(cliente, metodo, ruta, payload=None, params=None):
            enviado['payload'] = payload
            return 201, {'id': 'r1', 'deca': {'id': 'd1', 'referencia': 'DECA-R', 'version': 1, 'url': 'https://decafirma.test/d/d1'},
                         'paradas': [_envio('e1', [], ruta={'id': 'r1', 'parada': 1}), _envio('e2', [], ruta={'id': 'r1', 'parada': 2})]}

        with patch.object(type(ps), '_decafirma_pdf_odoo', side_effect=AssertionError('no tocaba')), patch.object(DecafirmaClient, '_request', falso):
            batch.action_decafirma_emitir_ruta()
        self.assertFalse(any('albaranPdf' in p for p in enviado['payload']['paradas']), 'sin albaranes en la ruta, no se manda ningún PDF')

    def test_ruta_con_pdfs_de_mas_los_ultimos_con_el_de_decafirma(self):
        self.company.decafirma_pdf_odoo = True
        ps = self._albaran(10) | self._albaran(20) | self._albaran(30)
        batch = self.env['stock.picking.batch'].create({'picking_ids': [(6, 0, ps.ids)], 'decafirma_plate': '4821KLM',
                                                        'decafirma_driver_id': self.conductor.id, 'decafirma_albaranes': True})
        enviado = {}

        def falso(cliente, metodo, ruta, payload=None, params=None):
            enviado['payload'] = payload
            return 201, {'id': 'r1', 'deca': {'id': 'd1', 'referencia': 'DECA-R', 'version': 1, 'url': 'https://decafirma.test/d/d1'},
                         'paradas': [_envio(f'e{i}', [], ruta={'id': 'r1', 'parada': i}) for i in (1, 2, 3)]}

        # Entre todos, como mucho 40 MB (aquí, 60 bytes): caben los dos primeros y el tercero lleva el de DecaFirma.
        pdf = b'%PDF-1.7 ' + b'x' * 21
        with patch('odoo.addons.decafirma_stock_batch.models.stock_picking_batch.MAX_PDFS_RUTA', 60), \
                patch.object(type(ps), '_decafirma_pdf_odoo', lambda rec: pdf), patch.object(DecafirmaClient, '_request', falso):
            batch.action_decafirma_emitir_ruta()
        self.assertEqual(['albaranPdf' in p for p in enviado['payload']['paradas']], [True, True, False])
        self.assertEqual(batch.decafirma_route_id, 'r1', 'la ruta sale igual')
        self.assertIn('pasan de 40 MB', ' '.join(ps[2].message_ids.mapped('body')))
