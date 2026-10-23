import tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from server import Store,SUBJECT
from test_server import image_upload
class SpecialTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name)
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def make(self,**kw):
  return self.store.material(dict(name='银行案例',category_id='B01',subject=SUBJECT,files=[image_upload(),image_upload('第二张.png','blue')],**kw))
 def test_migration_and_common_isolation(self):
  cats=self.store.all('category');self.assertEqual(16,sum(c['layer']=='special' for c in cats));self.assertEqual(4,len(self.store.all('industry')))
  self.store.migrate_special();self.assertEqual(26,len(self.store.all('category')))
  p={'id':'p','mappings':[{'category_id':'company_profile','confirmed':True,'material_ids':[],'action':'skip'}]};self.store.put('project',p)
  m=self.make();self.assertEqual(p,self.store.get('project','p'));self.assertEqual([],self.store.internal_materials())
  self.assertEqual(1,self.store.special_list({})['total'])
 def test_special_validation_and_cross_layer_block(self):
  value={'name':'某人员','subject':'某人员','category_id':'F02','valid_until':'2020-01-01','mode':'page'}
  feedback=self.store.validate_material_form(dict(value,upload_count=1));self.assertFalse(feedback['errors']);self.assertIn('valid_until',feedback['warnings'])
  feedback=self.store.validate_material_form(dict(value,category_id='company_profile',upload_count=1));self.assertIn('subject',feedback['errors']);self.assertIn('valid_until',feedback['errors'])
  m=self.make()
  with self.assertRaises(ValueError):self.store.material({'category_id':'company_profile'},m['id'])
 def test_filter_link_restore_rotation(self):
  m=self.make(linked_category_ids=['W01'],client_name='银行客户')
  self.assertEqual(1,self.store.special_list({'category_id':['W01'],'q':['银行客户']})['total'])
  a,b=[f['id'] for f in m['files']]
  m=self.store.material({'removed_file_ids':[a]},m['id']);self.assertEqual([a],m['removed_file_ids'])
  m=self.store.material({'restore_file_ids':[a],'rotations':{b:90}},m['id']);self.assertEqual([b,a],[f['id'] for f in m['files']])
  with self.assertRaises(ValueError):self.store.material({'restore_file_ids':['foreign']},m['id'])
  self.assertEqual(90,self.store.require('file',b)['rotation'])
  self.store.material({'archived':True},m['id']);self.assertEqual(0,self.store.special_list({})['total'])
  self.store.material({'archived':False},m['id']);self.assertEqual(1,self.store.special_list({})['total'])
 def test_import_replay_and_rollback(self):
  root=Path(self.tmp.name)/'source';root.mkdir();Image.new('RGB',(50,80),'white').save(root/'a.png')
  entry={'import_key':'stable','name':'业绩','subject':SUBJECT,'category_id':'I01','linked_category_ids':['F01'],'files':[{'path':'a.png','source':{'path':'来源.docx','locator':'P10'}}]}
  key,created=self.store.import_special(entry,root);self.assertTrue(created);self.assertEqual((key,False),self.store.import_special(entry,root))
  self.assertEqual(['F01'],self.store.get('material',key)['linked_category_ids'])
  self.assertEqual(1,self.store.special_list({'category_id':['F01']})['total'])
  before=len(self.store.all('file'))
  with self.assertRaises(FileNotFoundError):self.store.import_special(dict(entry,import_key='bad',files=entry['files']+[{'path':'missing.png'}]),root)
  self.assertEqual(before,len(self.store.all('file')))
if __name__=='__main__':unittest.main()
