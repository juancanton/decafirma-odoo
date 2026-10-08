import hashlib
import hmac
import json
from unittest.mock import patch

import base64

from odoo.exceptions import AccessError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.decafirma.models.client import DecafirmaClient


def _envio(id_='env1', docs=(), estado='documentado', ruta=None):
    return {'id': id_, 'estado': estado, 'ruta': ruta, 'documentos': list(docs)}


def _doc(id_, tipo, ref, version=1, estado='vigente', firmado=None):
    d = {'id': id_, 'tipo': tipo, 'referencia': ref, 'version': version, 'estado': estado, 'demo': False,
         'url': f'https://decafirma.test/d/{id_}', 'pdf': f'https://decafirma.test/api/v1/documentos/{id_}/pdf',
         'emitido': '2026-10-07T10:00:00Z'}
    if tipo == 'albaran':
        d['urlFirma'] = f'https://decafirma.test/d/{id_}/firmar'
        d['firmado'] = firmado
    return d


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
        self.assertEqual(x['lineas'][0]['referencia'], 'S25')
        self.assertEqual(x['destinationCity'], 'Granollers')

    def test_emitir_y_firma_por_aviso(self):
        p = self._albaran()
        llamadas = []

        def falso(cliente, metodo, ruta, payload=None, params=None):
            llamadas.append((metodo, ruta, payload))
            return 201, _envio(docs=[_doc('d1', 'deca', 'DECA-1'), _doc('a1', 'albaran', 'ALB-1')])

        with patch.object(DecafirmaClient, '_request', falso):
            p.action_decafirma_emitir_ambos()
        self.assertEqual(llamadas[0][0:2], ('POST', '/envios'))
        self.assertEqual(llamadas[0][2]['emitir'], 'ambos')
        self.assertIn(p.name, llamadas[0][2]['referencia'], 'la referencia lleva el albarán (y la base): idempotente')
        self.assertEqual(p.decafirma_state, 'documented')
        self.assertTrue(p.decafirma_has_deca and p.decafirma_has_albaran)
        self.assertEqual(p.decafirma_deca_url, 'https://decafirma.test/d/d1')

        # Lo que trae un aviso de firma: el PDF firmado vuelve adjunto
        with patch.object(DecafirmaClient, 'pdf', lambda cliente, url: b'%PDF-1.7 firmado'):
            p.decafirma_shipment_id._aplicar(_envio(docs=[
                _doc('d1', 'deca', 'DECA-1'),
                _doc('a1', 'albaran', 'ALB-1', version=2, firmado={'cuando': '2026-10-07T11:00:00Z', 'quien': 'Jordi Martí', 'dni': None, 'observaciones': 'Un saco roto'}),
            ]))
        self.assertTrue(p.decafirma_signed)
        mensaje = p.message_ids[0]
        self.assertIn('firmado por Jordi Martí', mensaje.body)
        self.assertIn('Un saco roto', mensaje.body)
        self.assertEqual(mensaje.attachment_ids.mapped('name'), ['Albaran ALB-1 firmado v2.pdf'], 'el PDF firmado, en el chatter')
        self.assertEqual(mensaje.attachment_ids.raw, b'%PDF-1.7 firmado')
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
            p.decafirma_shipment_id._aplicar(_envio(docs=[_doc('a1', 'albaran', 'ALB-1', 2, firmado={'cuando': '2026-10-07T11:00:00Z', 'quien': 'Ana'})]))
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
        self.assertEqual(base64.b64decode(enviado['albaranPdf']), pdf_odoo, 'se manda el PDF de Odoo')
        self.assertEqual(enviado['numeroAlbaran'], p.name, 'con su número, aunque no esté puesto el de Odoo')

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
        self.assertNotIn('albaranPdf', enviado)
        self.assertEqual(p.decafirma_state, 'documented')

    def test_sin_firmar_pdf_odoo_no_se_manda(self):
        p = self._albaran()
        enviado = {}

        def falso(cliente, metodo, ruta, payload=None, params=None):
            enviado.update(payload)
            return 201, _envio(docs=[_doc('a1', 'albaran', 'ALB-1')])

        with patch.object(type(p), '_decafirma_pdf_odoo', side_effect=AssertionError('no tocaba')), patch.object(DecafirmaClient, '_request', falso):
            p.action_decafirma_emitir_albaran()
        self.assertNotIn('albaranPdf', enviado)

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
        self.assertNotIn('albaranPdf', enviado)
        self.assertTrue(p.decafirma_shipment_id.document_ids, 'el albarán sale igual')
        self.assertIn('pesa más de 5 MB', ' '.join(p.message_ids.mapped('body')))

    def test_no_emite_dos_veces(self):
        p = self._albaran()
        with patch.object(DecafirmaClient, '_request', lambda *a, **k: (201, _envio(docs=[_doc('d1', 'deca', 'DECA-1')]))):
            p.action_decafirma_emitir_deca()
        with patch.object(DecafirmaClient, '_request', side_effect=AssertionError('no tenía que llamar')):
            p.action_decafirma_emitir_deca()

    def test_error_de_emision_se_dice(self):
        p = self._albaran()
        with patch.object(DecafirmaClient, '_request', lambda *a, **k: (422, dict(_envio(estado='borrador'), error='Falta la matrícula'))):
            r = p.action_decafirma_emitir_deca()
        self.assertEqual(r['params']['type'], 'warning')
        self.assertIn('Falta la matrícula', r['params']['message'])
        self.assertEqual(p.decafirma_error, 'Falta la matrícula')


def _papel(id_='pp1', numero='T-0042', peso='24380.00', facturado=None):
    return {'id': id_, 'tipo': 'ticket_bascula', 'numero': numero, 'emisor': 'Áridos del Vallès', 'fecha': '2026-10-08',
            'pesoNetoKg': peso, 'leido': True, 'mime': 'image/jpeg', 'subidoPor': 'el conductor',
            'subido': '2026-10-08T09:30:00Z', 'facturado': facturado, 'archivo': f'https://decafirma.test/api/v1/papeles/{id_}/archivo'}


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

        envio = dict(_envio(docs=[_doc('d1', 'deca', 'DECA-1')]), papeles=[_papel()])
        with patch.object(DecafirmaClient, 'archivo', archivo):
            p.decafirma_shipment_id._aplicar(envio)
            p.decafirma_shipment_id._aplicar(envio)  # el mismo aviso dos veces: un solo papel y una sola nota
        papel = p.decafirma_papel_ids
        self.assertEqual(len(papel), 1)
        self.assertEqual((papel.numero, papel.peso_neto_kg, papel.facturado), ('T-0042', 24380.0, False))
        self.assertEqual(pedidas, ['https://decafirma.test/api/v1/papeles/pp1/archivo'], 'la foto se baja una vez')
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
        self.assertFalse(p.decafirma_papel_ids, 'un plan sin papeles no trae «papeles» (o viene vacío)')

    def test_marcar_facturado(self):
        p = self._emitido()
        with patch.object(DecafirmaClient, 'archivo', lambda c, u: b'foto'):
            p.decafirma_shipment_id._aplicar(dict(_envio(docs=[_doc('d1', 'deca', 'DECA-1')]), papeles=[_papel()]))
        llamadas = []

        def falso(cliente, metodo, ruta, payload=None, params=None):
            llamadas.append((metodo, ruta, payload))
            return 200, _papel(facturado='2026-10-08T12:00:00Z' if payload['facturado'] else None)

        with patch.object(DecafirmaClient, '_request', falso):
            p.decafirma_papel_ids.action_marcar_facturado()
            p.decafirma_papel_ids.action_marcar_facturado()  # ya lo está: no vuelve a llamar
        self.assertEqual(llamadas, [('POST', '/papeles/pp1/facturado', {'facturado': True})])
        self.assertTrue(p.decafirma_papel_ids.facturado)
        with patch.object(DecafirmaClient, '_request', falso):
            p.decafirma_papel_ids.action_marcar_pendiente()
        self.assertFalse(p.decafirma_papel_ids.facturado, 'y se puede deshacer')

    def test_ponerse_al_dia_trae_los_papeles(self):
        p = self._emitido()
        envio_id = p.decafirma_shipment_id.deca_id

        def falso(cliente, metodo, ruta, payload=None, params=None):
            if ruta == '/documentos':
                return 200, {'documentos': [], 'hasta': '2026-10-08T10:00:00Z'}
            if ruta == '/papeles':
                return 200, {'papeles': [dict(_papel(), envio=envio_id)], 'hasta': '2026-10-08T10:00:00Z'}
            return 200, dict(_envio(docs=[_doc('d1', 'deca', 'DECA-1')]), papeles=[_papel()])

        with patch.object(DecafirmaClient, '_request', falso), patch.object(DecafirmaClient, 'archivo', lambda c, u: b'foto'):
            self.env['decafirma.shipment']._cron_poner_al_dia(self.company)
        self.assertEqual(p.decafirma_papel_ids.numero, 'T-0042', 'un aviso perdido se recupera en la tarea de cada 15 minutos')
        self.assertTrue(self.company.decafirma_last_sync_papeles)

    def test_sin_la_api_de_papeles_se_pone_al_dia_igual(self):
        p = self._emitido()
        from odoo.addons.decafirma.models.client import DecafirmaError

        def falso(cliente, metodo, ruta, payload=None, params=None):
            if ruta == '/papeles':
                raise DecafirmaError('DecaFirma: No existe (404)')
            if ruta == '/documentos':
                return 200, {'documentos': [{'envio': p.decafirma_shipment_id.deca_id}], 'hasta': '2026-10-08T10:00:00Z'}
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
        self.assertEqual(cabeceras['X-DecaFirma-Programa'], 'Odoo')
        self.assertEqual(cabeceras['X-DecaFirma-Usuario'], 'Mar%C3%ADa usuaria', 'quién emite, para «Emitido por María (desde Odoo)»')
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
        with patch.object(DecafirmaClient, '_request', lambda *a, **k: (200, _envio(docs=[_doc('d1', 'deca', 'DECA-1', 2, 'anulado')]))):
            self.env['decafirma.wizard'].with_user(jefa).create({'accion': 'anular', 'document_id': doc.id, 'motivo': 'No sale'}).action_confirmar()
        self.assertEqual(doc.state, 'anulado')

    def test_el_robot_no_se_presenta(self):
        self.assertNotIn('X-DecaFirma-Usuario', DecafirmaClient(self.company.with_user(self.env.ref('base.user_root')))._headers())


@tagged('post_install', '-at_install')
class TestDecafirmaWebhook(HttpCase):

    def test_firma_del_aviso(self):
        company = self.env.company
        company.sudo().decafirma_webhook_secret = 'whsec_prueba'
        envio = self.env['decafirma.shipment'].create({'name': 'X', 'res_model': 'res.partner', 'res_id': self.env.user.partner_id.id,
                                                       'deca_id': 'envW', 'company_id': company.id})
        cuerpo = json.dumps({'evento': 'documento.anulado', 'envio': _envio('envW', [_doc('dW', 'deca', 'DECA-W', 2, 'anulado')])}).encode()
        url = f'/decafirma/webhook/{company.id}'
        mala = self.url_open(url, data=cuerpo, headers={'Content-Type': 'application/json', 'X-DecaFirma-Firma': 'sha256=0'})
        self.assertEqual(mala.status_code, 401, 'sin la firma buena no se toca nada')
        firma = 'sha256=' + hmac.new(b'whsec_prueba', cuerpo, hashlib.sha256).hexdigest()
        buena = self.url_open(url, data=cuerpo, headers={'Content-Type': 'application/json', 'X-DecaFirma-Firma': firma})
        self.assertEqual(buena.status_code, 200)
        envio.invalidate_recordset()
        self.assertEqual(envio.document_ids.state, 'anulado')
