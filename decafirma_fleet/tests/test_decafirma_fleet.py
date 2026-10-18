from unittest.mock import patch

from odoo.tests import Form, tagged

from odoo.addons.decafirma.models.client import DecafirmaClient
from odoo.addons.decafirma_stock.tests.test_decafirma_stock import DecafirmaComun, SyncFalso, _doc, _envio


@tagged('post_install', '-at_install')
class TestDecafirmaFleet(DecafirmaComun):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        marca = cls.env['fleet.vehicle.model.brand'].create({'name': 'Iveco'})
        modelo = cls.env['fleet.vehicle.model'].create({'name': 'Eurocargo', 'brand_id': marca.id})
        cls.otro = cls.env['res.partner'].create({'name': 'Luis Gómez', 'vat': '87654321X'})
        cls.camion = cls.env['fleet.vehicle'].create({'model_id': modelo.id, 'license_plate': '1111BBB', 'driver_id': cls.otro.id})
        cls.remolque = cls.env['fleet.vehicle'].create({'model_id': modelo.id, 'license_plate': 'R9999ZZZ'})

    def test_elegir_el_camion_pone_matricula_y_conductor(self):
        p = self._albaran()
        with Form(p) as f:
            f.decafirma_vehicle_id = self.camion
            f.decafirma_trailer_vehicle_id = self.remolque
        self.assertEqual((p.decafirma_plate, p.decafirma_trailer, p.decafirma_driver_id), ('1111BBB', 'R9999ZZZ', self.otro))
        enviado = {}

        def falso(cliente, metodo, ruta, payload=None, params=None):
            enviado.update(payload)
            return 201, _envio(docs=[_doc('d1', 'deca', 'DECA-1')])

        with patch.object(DecafirmaClient, '_request', falso):
            p.action_decafirma_emitir_deca()
        self.assertEqual(enviado['plate'], '1111BBB')
        self.assertEqual(enviado['trailerPlate'], 'R9999ZZZ')
        self.assertEqual(enviado['driverName'], 'Luis Gómez')

    def test_sin_pantalla_tambien(self):
        p = self._albaran()
        p.write({'decafirma_plate': False, 'decafirma_vehicle_id': self.camion.id})
        self.assertEqual(p.decafirma_plate, '1111BBB', 'al guardar sin pantalla (importar, otra app), la matrícula de Flota')
        p.write({'decafirma_plate': '2222CCC'})
        self.assertEqual(p.decafirma_plate, '2222CCC', 'y se puede cambiar a mano')

    def test_en_la_ruta(self):
        ps = self._albaran(10) | self._albaran(20)
        batch = self.env['stock.picking.batch'].create({'picking_ids': [(6, 0, ps.ids)], 'decafirma_vehicle_id': self.camion.id})
        self.assertEqual((batch.decafirma_plate, batch.decafirma_driver_id), ('1111BBB', self.otro))

    # La puesta a punto: camiones con su chófer y autónomos con su camión.

    def _sync(self, falso, **vals):
        w = self.env['decafirma.sync.wizard'].create(dict(
            {'company_id': self.company.id, 'con_clientes': False, 'con_lugares': False, 'con_empresa': False,
             'con_ajustes': False}, **vals))
        with patch.object(DecafirmaClient, '_request', falso):
            w.action_mandar()
        return w, falso.llamadas[-1]

    def test_puesta_a_punto_camiones_y_autonomos(self):
        modelo = self.camion.model_id
        pepe = self.env['res.partner'].create({'name': 'Pepe Autónomo', 'vat': '11111111H', 'phone': '600111222',
                                               'decafirma_autonomo': True})
        suyo = self.env['fleet.vehicle'].create({'model_id': modelo.id, 'license_plate': '5555 FFF', 'driver_id': pepe.id})
        rosa = self.env['res.partner'].create({'name': 'Rosa Autónoma', 'decafirma_autonomo': True,
                                               'decafirma_autonomo_matricula': '7777GGG'})
        self.env['res.partner'].create({'name': 'Sin Camión', 'decafirma_autonomo': True})
        falso = SyncFalso()
        w = self.env['decafirma.sync.wizard'].create({'company_id': self.company.id})
        self.assertIn('3 autónomos, 2 camiones (1 no se mandan)', w.cuanto_autonomos)
        w, cuerpo = self._sync(falso)
        personas = {p['externalId']: p for p in cuerpo['people']}
        vehiculos = {v['plate']: v for v in cuerpo['vehicles']}
        luis = personas[f'odoo:res.partner:{self.otro.id}']
        self.assertEqual((luis['kind'], luis['name'], luis['taxId']), ('driver', 'Luis Gómez', '87654321X'))
        self.assertEqual(vehiculos['1111BBB']['driverExternalId'], f'odoo:res.partner:{self.otro.id}', 'el camión con su chófer')
        self.assertEqual(vehiculos['1111BBB']['description'], modelo.display_name)
        self.assertNotIn('driverExternalId', vehiculos['R9999ZZZ'], 'el remolque, sin chófer')
        self.assertEqual(personas[f'odoo:res.partner:{pepe.id}']['kind'], 'carrier', 'el autónomo, transportista')
        self.assertEqual(vehiculos['5555FFF']['driverExternalId'], f'odoo:res.partner:{pepe.id}', 'con su camión de Flota')
        self.assertEqual(vehiculos['5555FFF']['externalId'], f'odoo:fleet.vehicle:{suyo.id}')
        self.assertEqual(vehiculos['7777GGG']['driverExternalId'], f'odoo:res.partner:{rosa.id}', 'o con la matrícula de su ficha')
        self.assertEqual(len([p for p in cuerpo['people'] if p['externalId'] == f'odoo:res.partner:{pepe.id}']), 1)
        self.assertIn('Sin Camión', w.resumen)
        self.assertIn('Autónomo sin camión', w.resumen)
        # Cada vehículo va en la misma tanda que su conductor (aquí, todo en una).
        self.assertEqual(len(falso.llamadas), 1)

        # Repetir no cambia nada; un camión archivado en Flota, se archiva.
        links = {lk.id: lk.write_date for lk in self.env['decafirma.sync.link'].search([])}
        _w, cuerpo = self._sync(falso)
        self.assertEqual({lk.id: lk.write_date for lk in self.env['decafirma.sync.link'].search([])}, links)
        self.remolque.active = False
        _w, cuerpo = self._sync(falso)
        remolque = [v for v in cuerpo['vehicles'] if v['externalId'] == f'odoo:fleet.vehicle:{self.remolque.id}']
        self.assertTrue(remolque[0]['archived'])

    def test_sin_flota_marcada_no_van(self):
        _w, cuerpo = self._sync(SyncFalso(), con_camiones=False, con_autonomos=False, con_ajustes=True)
        self.assertNotIn('vehicles', cuerpo)
        self.assertNotIn('people', cuerpo)
