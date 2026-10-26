from pathlib import Path
import base64,copy,json,shutil,sqlite3,sys,os
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'标书管理系统'))
from server import Store
import engine
TMP=Path(os.environ['BID_AUTH_QA_DIR'])
DATA=TMP/'data'; DATA.mkdir(exist_ok=True)
c=sqlite3.connect('file:'+str(ROOT/'标书管理系统/data/store.sqlite3')+'?mode=ro',uri=True)
objects={(kind,key):json.loads(raw) for kind,key,raw in c.execute('select kind,id,payload from objects')};c.close()
store=Store(DATA,seed=False)
for cat in store.all('category'):
 if cat.get('layer')=='common':cat['enabled']=cat['id']=='authorization';store.put('category',cat)
ids=['seed_sf_001','material_41b78af9f084422ca586b61f49647d28']
for mid in ids:
 m=copy.deepcopy(objects['material',mid]);store.put('material',m)
 for fid in m['file_ids']:
  f=objects['file',fid];dest=DATA/f['storage'];dest.parent.mkdir(parents=True,exist_ok=True)
  shutil.copy2(ROOT/'标书管理系统/data'/f['storage'],dest);store.put('file',f)
store.db.commit()
cases=[]
# Images go only to a labelled agent ID slot; the letter body is for the text template.
for label,needles,slot in [
 ('东方基金',['东方基金'],False),
 ('太平洋健康2026',['直播供应商（增补）'],False),
 ('宁波银行',['宁波银行','第三次'],True)]:
 source=next(p for p in (ROOT/'标书管理系统/考题').glob('*/考题.docx') if all(n in str(p) for n in needles))
 p=store.create_project({'name':'授权书验证_'+label,'filename':source.name,'data':base64.b64encode(source.read_bytes()).decode()})
 cases.append({'label':label,'id':p['id'],'source':str(source),'slot':slot})
# Simulate pre-removal saved settings; opening the project must discard them.
legacy=store.require('project', cases[0]['id'])
row=next(r for r in legacy['mappings'] if r['category_id']=='authorization')
row.update(authorization={'enabled':True,'signed_on':''},
           authorization_preview={'body_lines':['旧正文不得导出']},
           material_ids=ids,action='fill',confirmed=True)
store.put('project',legacy);store.db.commit()
(TMP/'cases.json').write_text(json.dumps({'cases':cases,'ids':ids},ensure_ascii=False,indent=2))
store.close()
