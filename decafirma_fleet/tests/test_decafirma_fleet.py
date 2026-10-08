from unittest.mock import patch

from odoo.tests import Form, tagged

from odoo.addons.decafirma.models.client import DecafirmaClient
from odoo.addons.decafirma_stock.tests.test_decafirma_stock import DecafirmaComun, _doc, _envio


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
