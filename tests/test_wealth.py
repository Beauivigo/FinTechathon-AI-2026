from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from bank.store import Store
from bank.seed import initialize
from bank.gateway import BankGateway
from shared import ServiceError,adapt_request
from wealth import products,compare
from tests.test_app import AppTest


class WealthTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.store=Store(Path(temp.name)/'bank.sqlite3'); initialize(self.store)
        self.gw=BankGateway(self.store)

    def test_selection_order_copy_and_no_write(self):
        req=adapt_request('wealth.compare',{'product_ids':['sample_product_3','product_1']},request_id='r',trace_id='t')
        before=self.store.path.read_bytes()
        result=self.gw.execute(req,principal_id='demo_user_a')
        self.assertEqual([p['product_id'] for p in result['data']['products']],req['params']['product_ids'])
        result['data']['products'][0]['risk_level']='R5'
        self.assertEqual(products()[2]['risk_level'],'R4')
        self.assertEqual(self.store.path.read_bytes(),before)
        self.assertEqual(len(self.gw.get_context(principal_id='demo_user_a')['products']),3)

    def test_invalid_ids_and_identity(self):
        for ids in [[],['product_1','product_1'],['missing']]:
            with self.assertRaises(ServiceError): compare({'product_ids':ids})
        req=adapt_request('wealth.compare',{'product_ids':['product_1']},request_id='r',trace_id='t')
        self.assertEqual(self.gw.execute(req,principal_id='missing')['status'],'failed')

    @unittest.skipIf(AppTest is None,'页面依赖未安装')
    def test_page_table_and_selection_change(self):
        with patch('app.demo.gateway',return_value=self.gw):
            app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app/pages/2_产品对比.py')).run()
            app.button(key='product_compare').click().run()
            self.assertFalse(app.exception); self.assertEqual(len(app.dataframe[0].value),2)
            app.multiselect(key='product_ids').set_value([]).run()
            self.assertFalse(app.dataframe); self.assertTrue(app.button(key='product_compare').disabled)


if __name__=='__main__': unittest.main()
