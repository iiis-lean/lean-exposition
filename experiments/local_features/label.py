"""Bounded real dual-model labeling, with independent tool-free Codex batches."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
import time

from lean_exposition.runtime import RunConfig, Runtime
from prepare import OUT, digest
from protocol import PROMPT, PROTOCOL_VERSION, SCHEMA


def write_json(path,payload):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(payload,ensure_ascii=False,indent=2))
    temporary.replace(path)


def credentials():
    for line in Path('/root/.config/lean-exposition/bigmodel.env').read_text().splitlines():
        line=line.strip()
        if line and not line.startswith('#'):
            key,value=line.removeprefix('export ').split('=',1)
            os.environ[key.strip()]=value.strip().strip('\"\'')


def run_batch(provider,index,cards):
    schema=json.loads(json.dumps(SCHEMA)); schema['properties']['labels']['minItems']=len(cards);schema['properties']['labels']['maxItems']=len(cards)
    prompt=PROMPT+json.dumps(cards,ensure_ascii=False,sort_keys=True)
    fingerprint=digest({'prompt':prompt,'schema':schema,'protocol':PROTOCOL_VERSION})
    dest=OUT/'runs'/provider;dest.mkdir(parents=True,exist_ok=True)
    final=dest/f'batch_{index:02d}.json'
    request_config={'model':'glm-5.3-flash' if provider=='glm'else 'gpt-5.6-luna','reasoning':'max','max_output_tokens':16384 if provider=='glm'else None,'timeout':480}
    if final.exists():
        old=json.loads(final.read_text())
        if old['input_digest']==fingerprint and old['valid'] and (provider=='luna' or old.get('request_config')==request_config):
            return provider,index,'cached'
    attempts=[]
    existing=[int(p.stem.rsplit("_",1)[1])for p in dest.glob(f"batch_{index:02d}_attempt_*.json")]
    offset=max(existing,default=-1)+1
    prior_matching=sum(json.loads(p.read_text()).get("request_config")==request_config for p in dest.glob(f"batch_{index:02d}_attempt_*.json"))
    for attempt in range(offset,offset+max(0,3-prior_matching)):
        with tempfile.TemporaryDirectory(prefix='lean-label-'+provider+'-')as directory:
            if provider=='glm':
                config=RunConfig(provider='openai_api',model='glm-5.3-flash',api_mode='chat_completions',base_url='https://open.bigmodel.cn/api/paas/v4/',credential_env='BIGMODEL_API_KEY',timeout=480,max_output_tokens=16384,api_extra_body={"reasoning_effort":"max"})
            else:
                config=RunConfig(provider='codex_agent',model='gpt-5.6-luna',reasoning='max',codex_home=directory+'/.codex',cwd=directory+'/work',auth_path='/root/.codex/auth.json',timeout=480,max_output_tokens=8192)
            runtime=Runtime();started=time.monotonic();result=runtime.result(runtime.start(prompt,schema,config))
            data=result.data
            valid=result.state=='succeeded' and [r['sample_id']for r in data['labels']]==[r['sample_id']for r in cards] and all(r[k]['evidence'].strip()for r in data['labels']for k in ['A','B','C','D'])
            record={'provider':provider,'model':config.model,'reasoning':'max','request_config':request_config,'protocol':PROTOCOL_VERSION,
                'schema_digest':digest(schema),'input_digest':fingerprint,'batch':index,'attempt':attempt,'prompt':prompt,'schema':schema,'sample_ids':[c['sample_id']for c in cards],
                'result':asdict(result),'valid':valid,'validation_error':None if valid else 'provider/schema failure or output sample order/identity mismatch','seconds':time.monotonic()-started}
            write_json(dest/f'batch_{index:02d}_attempt_{attempt}.json',record)
            attempts.append({'attempt':attempt,'valid':valid,'state':result.state,'error':result.error})
            if config.codex_home:
                (Path(config.codex_home)/'auth.json').unlink(missing_ok=True)
            if valid:
                record['attempts']=attempts
                write_json(final,record)
                return provider,index,'succeeded'
    return provider,index,'failed'


def consolidate():
    for provider in ['glm','luna']:
        labels=[]
        for path in sorted((OUT/'runs'/provider).glob('batch_??.json')):
            record=json.loads(path.read_text())
            for label in record['result']['data']['labels']:
                labels.append({**label,'provider':provider,'model':record['model'],'reasoning':record['reasoning'],'protocol':record['protocol'],'input_digest':record['input_digest'],'batch':record['batch'],'evidence_file':str(path)})
        write_json(OUT/f'labels_{provider}.json',labels)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--pilot',action='store_true');parser.add_argument('--workers',type=int,default=2);args=parser.parse_args()
    if not 1<=args.workers<=3:raise ValueError('concurrency must be 1..3')
    credentials();cards=json.loads((OUT/'cards.json').read_text())['cards'];batches=[cards[i:i+5]for i in range(0,len(cards),5)]
    selected=batches[:1]if args.pilot else batches
    with ThreadPoolExecutor(max_workers=args.workers)as pool:
        futures=[pool.submit(run_batch,provider,index,batch)for index,batch in enumerate(selected)for provider in ['glm','luna']]
        for future in as_completed(futures):
            print(json.dumps(future.result()),flush=True);consolidate()
    consolidate()

if __name__=='__main__':main()
