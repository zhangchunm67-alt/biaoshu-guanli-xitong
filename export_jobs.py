"""Persistent local export jobs; document work never holds the application DB lock."""
import copy
import math
import time
import uuid
from concurrent.futures import ThreadPoolExecutor


class ExportJobs:
    def __init__(self, store):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='bid-export')
        for job in store.all('export_job'):
            if job['status'] in {'queued', 'running'}:
                job.update(status='failed', stage='导出已中断，请重新导出', error='本地服务曾停止，本次导出未完成。')
                store.put('export_job', job)
        store.db.commit()

    def close(self):
        self.pool.shutdown(wait=True)

    def list(self):
        jobs=list(reversed(self.store.all('export_job')))
        active=[j for j in jobs if j['status'] in {'queued','running'}]
        finished=[j for j in jobs if j['status'] not in {'queued','running'}][:10]
        return [self.public(j) for j in active+finished]

    def public(self, job):
        value = copy.deepcopy(job)
        if job['status'] == 'running':
            elapsed = max(0, time.time() - job['started_at'])
            p = job['progress'] / 100
            estimate = job['estimate_seconds']
            if p >= .2:
                estimate = max(estimate, elapsed / p)
            value['remaining_seconds'] = (None if p < .2 and elapsed > estimate
                                          else max(1, math.ceil(estimate - elapsed)))
        elif job['status'] == 'queued':
            value['remaining_seconds'] = None
        else:
            value['remaining_seconds'] = 0
        return value

    def start(self, key, body):
        for job in self.store.all('export_job'):
            if job['project_id'] == key and job['status'] in {'queued', 'running'}:
                return self.public(job)
        snapshot = self.store.prepare_export(key, body)
        project, materials, categories, mappings, mode = snapshot
        selected = {mid for row in mappings for mid in row.get('material_ids', [])}
        files = sum(len(m.get('files', [])) for m in materials if m['id'] in selected)
        weight = max(1, len(mappings) + files * 2)
        history = [j for j in self.store.all('export_job') if j['status'] == 'completed' and j.get('duration')]
        estimate = max(3, (sum(j['duration']/j['weight'] for j in history[-10:])/len(history[-10:])*weight) if history else 3 + weight * .4)
        job = dict(id='export_job_'+uuid.uuid4().hex, project_id=key, project_name=project['name'],
                   mode=mode, status='queued', stage='等待导出', progress=0, created_at=time.time(),
                   estimate_seconds=estimate, weight=weight)
        self.store.put('export_job', job); self.store.db.commit()
        self.pool.submit(self.run, job['id'], snapshot)
        return self.public(job)

    def run(self, key, snapshot):
        def update(**values):
            with self.store.lock:
                job=self.store.require('export_job',key)
                job.update(values)
                self.store.put('export_job',job); self.store.db.commit()
        started=time.time()
        update(status='running', started_at=started, stage='正在读取模板', progress=2)
        try:
            result=self.store.export(snapshot[0]['id'], {}, snapshot=snapshot,
                progress=lambda percent,stage:update(progress=percent,stage=stage))
            update(status='completed',progress=100,stage='导出完成',result=result,duration=time.time()-started)
        except Exception as exc:
            message=str(exc) if isinstance(exc,ValueError) else '导出失败，请重试；原始材料未修改。'
            update(status='failed',stage='导出失败',error=message)
