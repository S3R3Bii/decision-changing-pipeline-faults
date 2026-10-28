"""Exhaustive case reconstruction, SQL feed checks and independent flag/metric arithmetic."""
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
import argparse
import csv
import hashlib
import json
import math
import sqlite3
import statistics as st

import numpy as np

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'results'
PRODUCTS=['Checking or savings account','Debt collection','Money transfer, virtual currency, or money service','Mortgage','Student loan','Vehicle loan or lease']

def read(name):
    with open(OUT/name,encoding='utf-8-sig',newline='') as handle:return list(csv.DictReader(handle))

def close(a,b):
    if a is None:assert b in [None,''];return
    assert math.isclose(float(a),float(b),rel_tol=1e-9,abs_tol=1e-7),(a,b)

def source():
    manifest=(ROOT/'data/manifest.json').read_bytes();records=json.loads(manifest);panel={}
    assert len(records)==102;all_counts=Counter()
    for item in records:
        blob=(ROOT/item['file']).read_bytes();assert hashlib.sha256(blob).hexdigest()==item['sha256'];raw=json.loads(blob)
        assert sum(raw['product_counts'].values())==raw['total_complaints'];assert raw['aggregation_error_bound']==raw['omitted_bucket_count']==0
        values={p:int(raw['product_counts'][p]) for p in PRODUCTS};assert all(v>=0 for v in values.values());panel[raw['period_start_inclusive'][:7]]=values;all_counts.update(raw['product_counts'])
    receipt=json.loads((ROOT/'data/upstream_source_reconciliation.json').read_text());assert receipt['status']=='PASS' and not receipt['differences'];assert hashlib.sha256(manifest).hexdigest()==receipt['manifest_sha256'];assert sum(all_counts.values())==receipt['monthly_total']==receipt['whole_period_total'];return panel

def independent_cases(phase,panel):
    exported=json.loads((OUT/(phase+'_cases.json')).read_text());months=sorted(panel);result={};sql=(ROOT/'sql/feed_contract.sql').read_text();db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE expected_products(product TEXT PRIMARY KEY)');db.executemany('INSERT INTO expected_products VALUES(?)',[(p,) for p in PRODUCTS]);db.execute('CREATE TABLE feed(product TEXT,period TEXT,count_value)')
    expected_families=['unmodified','duplicate_largest','drop_largest','scale_double','replay']+(['scale_half','exchange_labels'] if phase=='test' else [])
    allowed=[m for m in months if ('2021-01'<=m<='2023-12' if phase=='validation' else m>='2024-01')]
    assert {r['case_id'] for r in exported}=={m+'_'+f for m in allowed for f in expected_families}
    for record in exported:
        month=record['month'];index=months.index(month);reference=panel[month];previous=panel[months[index-1]];old=panel[months[index-2]];family=record['family'];largest=sorted(PRODUCTS,key=lambda p:(-reference[p],p))[0];smallest=sorted(PRODUCTS,key=lambda p:(reference[p],p))[0]
        counts=dict(reference);bag=[(p,reference[p]) for p in PRODUCTS];control=sum(reference.values())
        if family=='duplicate_largest':bag.append((largest,reference[largest]))
        elif family=='drop_largest':bag=[(p,v) for p,v in bag if p!=largest]
        elif family=='scale_double':bag=[(p,2*v) for p,v in bag];control=sum(v for _,v in bag)
        elif family=='scale_half':bag=[(p,math.floor(Fraction(v,2))) for p,v in bag];control=sum(v for _,v in bag)
        elif family=='exchange_labels':bag=[(p,reference[smallest] if p==largest else reference[largest] if p==smallest else v) for p,v in bag]
        elif family=='replay':bag=list(previous.items());control=sum(previous.values())
        batch=record['batch'];assert batch['month']==month and batch['control_total']==control
        assert sorted((r['product'],r['count']) for r in batch['rows'])==sorted(bag);assert all(r['period']==month for r in batch['rows'])
        db.execute('DELETE FROM feed');db.executemany('INSERT INTO feed VALUES(?,?,?)',[(r['product'],r['period'],r['count']) for r in batch['rows']]);contract=dict(db.execute(sql,(month,)).fetchone())
        bad_structure=not(contract['row_count']==contract['distinct_products']==6 and contract['payload_total']==control and contract['invalid_counts']==contract['wrong_periods']==contract['unknown_products']==0)
        summed=Counter()
        for p,value in bag:summed[p]+=value
        reserve_reference={p:math.ceil(Fraction(reference[p]+previous[p]+old[p],3)) for p in PRODUCTS}
        reserve_corrupted={p:math.ceil(Fraction(summed[p]+previous[p]+old[p],3)) for p in PRODUCTS}
        assert reserve_reference==record['reference_reserve'] and reserve_corrupted==record['corrupted_reserve']
        delta=[reserve_corrupted[p]-reserve_reference[p] for p in PRODUCTS];change=sum(abs(v) for v in delta);mse=float(Fraction(sum(v*v for v in delta),6));ratio=Fraction(change,sum(reserve_reference.values()))
        assert change==record['decision_change'] and (ratio>Fraction(1,20))==record['material_change'];close(mse,record['decision_mse']);close(float(ratio),record['normalized_change'])
        result[record['case_id']]=dict(record,sql_structure_flag=bad_structure)
    return result

def independent_flag(config,case,panel):
    if case['sql_structure_flag']:return True
    if config['kind']=='structural':return False
    months=sorted(panel);i=months.index(case['month']);batch={r['product']:r['count'] for r in case['batch']['rows']}
    if all(batch[p]==panel[months[i-1]][p] for p in PRODUCTS):return True
    if config['kind']=='replay':return False
    window=int(float(config['window']));cutoff=float(config['cutoff']);relative=float(config['relative'])
    matrix=np.array([[panel[m][p] for p in PRODUCTS] for m in months[i-window:i]],dtype=float);assert len(matrix)==window
    transformed=np.log1p(matrix);center=np.median(transformed,axis=0);mad=np.median(abs(transformed-center),axis=0);scales=np.maximum(.05,1.4826*mad);count_center=np.median(matrix,axis=0);current=np.array([batch[p] for p in PRODUCTS],dtype=float)
    return bool(np.any((abs(np.log1p(current)-center)/scales>cutoff)&(abs(current-count_center)/np.maximum(1,count_center)>relative)))

def independent_metrics(rows):
    faulty=[r for r in rows if r['family']!='unmodified'];background=[r for r in rows if r['family']=='unmodified'];material=[r for r in faulty if r['material_change']=='True'];impact=sum(int(r['decision_change']) for r in faulty)
    signals=lambda items:sum(r['flag']=='True' for r in items)
    return dict(fault_cases=len(faulty),flagged_faults=signals(faulty),fault_recall=signals(faulty)/len(faulty) if faulty else None,decision_weighted_recall=sum(int(r['decision_change'])*(r['flag']=='True') for r in faulty)/impact if impact else None,unflagged_decision_change=sum(int(r['decision_change']) for r in faulty if r['flag']=='False'),total_decision_change=impact,unflagged_decision_mse=sum(float(r['decision_mse']) for r in faulty if r['flag']=='False')/len(faulty) if faulty else None,material_cases=len(material),material_recall=signals(material)/len(material) if material else None,background_months=len(background),background_flags=signals(background),background_burden=signals(background)/len(background) if background else None)

def check_flags(phase,panel,cases,configs):
    rows=read('validation_flags.csv' if phase=='validation' else 'test_flags.csv')
    for r in rows:
        case=cases[r['case_id']];assert independent_flag(configs[r['monitor']],case,panel)==(r['flag']=='True')
        for name in ['decision_change','normalized_change','decision_mse']:close(case[name],r[name])
        assert case['material_change']==(r['material_change']=='True') and case['month']==r['month'] and case['family']==r['family'] and case['mechanism_group']==r['mechanism_group']
    return rows

def validation():
    panel=source();cases=independent_cases('validation',panel);trials=read('search.csv');configs={r['name']:r for r in trials};rows=check_flags('validation',panel,cases,configs);scores={}
    assert len(trials)==14 and len(rows)==14*180
    for trial in trials:
        subset=[r for r in rows if r['monitor']==trial['name']];assert len(subset)==len(cases);score=independent_metrics(subset);scores[trial['name']]=score
        for key,value in score.items():close(value,trial[key])
    eligible=[name for name,score in scores.items() if score['background_burden']<=.05];winner=min(eligible,key=lambda n:(-scores[n]['decision_weighted_recall'],scores[n]['background_burden'],n));selection=json.loads((OUT/'selection.json').read_text());assert winner==selection['winner'] and selection['candidates']==14
    return dict(status='PASS',candidate_count=14,flags_checked=len(rows),synthetic_cases_reconstructed=len(cases),decisions_and_mse='Every reference/corrupted reserve vector, exact integer change, normalized change and MSE reconstructed with rational arithmetic',sql='Every feed grain/count/period/header contract independently evaluated',selection='Every constraint/objective and the frozen winner recomputed')

def test():
    panel=source();cases=independent_cases('test',panel);configs={r['name']:r for r in read('search.csv')};rows=check_flags('test',panel,cases,configs);summary=json.loads((OUT/'test_summary.json').read_text());selection=json.loads((OUT/'selection.json').read_text())
    for expected in summary['metrics']:
        subset=[r for r in rows if r['monitor']==expected['monitor']]
        if expected['stratum']=='family':subset=[r for r in subset if r['family']==expected['value']]
        elif expected['stratum']=='mechanism':subset=[r for r in subset if r['mechanism_group']==expected['value']]
        score=independent_metrics(subset)
        for key,value in score.items():close(value,expected[key])
    selected={r['case_id']:r for r in rows if r['monitor']==selection['winner'] and r['family']!='unmodified'};baseline={r['case_id']:r for r in rows if r['monitor']=='structural' and r['family']!='unmodified'};quarter=defaultdict(lambda:dict(impact=0,selected_detected_impact=0,baseline_detected_impact=0))
    for key,row in selected.items():
        year,month=map(int,row['month'].split('-'));q=f'{year}Q{(month-1)//3+1}';impact=int(row['decision_change']);quarter[q]['impact']+=impact;quarter[q]['selected_detected_impact']+=impact*(row['flag']=='True');quarter[q]['baseline_detected_impact']+=impact*(baseline[key]['flag']=='True')
    blocks=read('quarter_blocks.csv');assert len(blocks)==len(quarter)==10
    for block in blocks:
        for key,value in quarter[block['quarter']].items():close(value,block[key])
    total=sum(x['impact'] for x in quarter.values());point=sum(x['selected_detected_impact']-x['baseline_detected_impact'] for x in quarter.values())/total;close(point,summary['bootstrap']['mean']);values=[]
    for draw in read('bootstrap_draws.csv'):
        indices=[int(x) for x in draw['quarter_indices'].split()];assert len(indices)==10
        impact=sum(int(blocks[i]['impact']) for i in indices);gain=sum(int(blocks[i]['selected_detected_impact'])-int(blocks[i]['baseline_detected_impact']) for i in indices)/impact;close(gain,draw['weighted_recall_gain']);values.append(gain)
    assert len(values)==2000;values.sort()
    for p,value in zip([.025,.975],summary['bootstrap']['percentile95']):
        pos=(len(values)-1)*p;lo=math.floor(pos);hi=math.ceil(pos);close(values[lo]+(pos-lo)*(values[hi]-values[lo]),value)
    return dict(status='PASS',test_cases_reconstructed=len(cases),flags_checked=len(rows),metric_rows_checked=len(summary['metrics']),quarter_blocks_checked=10,bootstrap_draws_checked=2000,scope='Exhaustive synthetic mutation, SQL contract, independent monitor flag, rational decision/MSE and metric/block reconstruction')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['validation','test']);phase=parser.parse_args().phase;result=globals()[phase]();(OUT/f'verification_{phase}.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
