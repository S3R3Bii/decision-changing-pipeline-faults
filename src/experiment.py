"""Controlled pipeline faults and decision-weighted monitoring under a review budget."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import argparse
import csv
import hashlib
import json
import math
import statistics
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results'
SEED = 20261028
PRODUCTS = ['Checking or savings account', 'Debt collection',
            'Money transfer, virtual currency, or money service', 'Mortgage',
            'Student loan', 'Vehicle loan or lease']
VALIDATION_FAMILIES = ['duplicate_largest', 'drop_largest', 'scale_double', 'replay']
TEST_FAMILIES = VALIDATION_FAMILIES + ['scale_half', 'exchange_labels']


def write_json(name, value):
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def write_csv(name, rows):
    assert rows
    with open(OUT / name, 'w', newline='', encoding='utf-8') as handle:
        fieldnames = list(dict.fromkeys(key for row in rows for key in row))
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def load_source():
    manifest = json.loads((ROOT / 'data/manifest.json').read_text())
    assert len(manifest) == 102
    months, panel = [], []
    all_counts = Counter()
    for item in manifest:
        blob = (ROOT / item['file']).read_bytes()
        assert hashlib.sha256(blob).hexdigest() == item['sha256']
        raw = json.loads(blob)
        assert raw['aggregation_error_bound'] == raw['omitted_bucket_count'] == 0
        assert sum(raw['product_counts'].values()) == raw['total_complaints']
        assert all(p in raw['product_counts'] for p in PRODUCTS)
        all_counts.update(raw['product_counts'])
        months.append(raw['period_start_inclusive'][:7])
        panel.append({p: raw['product_counts'][p] for p in PRODUCTS})
    assert months == sorted(months) and len(set(months)) == 102
    expected = [f'{year}-{month:02}' for year in range(2018, 2027) for month in range(1, 13) if f'{year}-{month:02}' <= '2026-06']
    assert months == expected
    reconciliation = json.loads((ROOT / 'data/upstream_source_reconciliation.json').read_text())
    assert reconciliation['status'] == 'PASS' and not reconciliation['differences']
    assert sum(all_counts.values()) == reconciliation['monthly_total'] == reconciliation['whole_period_total']
    return months, panel


def make_batch(counts, month):
    return {'month': month, 'control_total': sum(counts.values()),
            'rows': [{'product': p, 'period': month, 'count': counts[p]} for p in PRODUCTS]}


def mutate(counts, previous, month, family):
    batch = make_batch(counts, month)
    largest = min(PRODUCTS, key=lambda p: (-counts[p], p))
    smallest = min(PRODUCTS, key=lambda p: (counts[p], p))
    if family == 'duplicate_largest':
        batch['rows'].append(dict(next(r for r in batch['rows'] if r['product'] == largest)))
    elif family == 'drop_largest':
        batch['rows'] = [r for r in batch['rows'] if r['product'] != largest]
    elif family == 'scale_double':
        for row in batch['rows']: row['count'] *= 2
        batch['control_total'] = sum(r['count'] for r in batch['rows'])
    elif family == 'scale_half':
        for row in batch['rows']: row['count'] //= 2
        batch['control_total'] = sum(r['count'] for r in batch['rows'])
    elif family == 'exchange_labels':
        for row in batch['rows']:
            if row['product'] == largest: row['count'] = counts[smallest]
            elif row['product'] == smallest: row['count'] = counts[largest]
    elif family == 'replay':
        batch = make_batch(previous, month)
    elif family != 'unmodified':
        raise ValueError(family)
    return batch


def structural(batch, expected_month):
    rows = batch['rows']
    return not (batch['month'] == expected_month and len(rows) == 6 and
                {r['product'] for r in rows} == set(PRODUCTS) and
                all(r['period'] == expected_month and type(r['count']) is int and r['count'] >= 0 for r in rows) and
                sum(r['count'] for r in rows) == batch['control_total'])


def reserve(rows, prior_two):
    current = Counter()
    for row in rows: current[row['product']] += row['count']
    return {p: (current[p] + prior_two[-1][p] + prior_two[-2][p] + 2) // 3 for p in PRODUCTS}


def decision_evidence(batch, reference, prior_two):
    a = reserve(make_batch(reference, batch['month'])['rows'], prior_two)
    b = reserve(batch['rows'], prior_two)
    delta = [b[p] - a[p] for p in PRODUCTS]
    change = sum(abs(x) for x in delta)
    return {'decision_change': change, 'normalized_change': change / sum(a.values()),
            'decision_mse': sum(x*x for x in delta) / 6, 'material_change': change / sum(a.values()) > .05,
            'reference_reserve': a, 'corrupted_reserve': b}


def candidates():
    result = [dict(name='structural', kind='structural'), dict(name='structural_replay', kind='replay')]
    for window in [6, 12]:
        for cutoff in [3, 5, 8]:
            for relative in [.10, .25]:
                result.append(dict(name=f'robust_w{window}_z{cutoff}_r{relative}', kind='numeric',
                                   window=window, cutoff=cutoff, relative=relative))
    return result


def flag(config, batch, history, month):
    if structural(batch, month): return True
    if config['kind'] == 'structural': return False
    current = {r['product']: r['count'] for r in batch['rows']}
    if current == history[-1]: return True
    if config['kind'] == 'replay': return False
    prior = history[-config['window']:]
    assert len(prior) == config['window']
    for p in PRODUCTS:
        logs = [math.log1p(row[p]) for row in prior]
        center = statistics.median(logs)
        scale = max(.05, 1.4826 * statistics.median(abs(x-center) for x in logs))
        count_center = statistics.median(row[p] for row in prior)
        z = abs(math.log1p(current[p]) - center) / scale
        relative = abs(current[p] - count_center) / max(1, count_center)
        if z > config['cutoff'] and relative > config['relative']: return True
    return False


def cases(phase, months, panel):
    families = VALIDATION_FAMILIES if phase == 'validation' else TEST_FAMILIES
    result = []
    for index, month in enumerate(months):
        active = '2021-01' <= month <= '2023-12' if phase == 'validation' else month >= '2024-01'
        if not active: continue
        for family in ['unmodified'] + families:
            batch = mutate(panel[index], panel[index-1], month, family)
            decision = decision_evidence(batch, panel[index], panel[:index][-2:])
            result.append(dict(case_id=month+'_'+family, month=month, index=index, family=family,
                               mechanism_group='held_family' if family in ['scale_half','exchange_labels'] else ('unmodified' if family == 'unmodified' else 'validation_family'),
                               **decision, batch=batch))
    return result


def evaluate(config, case_rows, panel):
    result = []
    for case in case_rows:
        signal = flag(config, case['batch'], panel[:case['index']], case['month'])
        result.append({k: case[k] for k in ['case_id','month','family','mechanism_group','decision_change','normalized_change','decision_mse','material_change']} | {'monitor':config['name'],'flag':signal})
    return result


def metrics(rows):
    faulty = [r for r in rows if r['family'] != 'unmodified']
    background = [r for r in rows if r['family'] == 'unmodified']
    material = [r for r in faulty if r['material_change']]
    impact = sum(r['decision_change'] for r in faulty)
    return dict(fault_cases=len(faulty), flagged_faults=sum(r['flag'] for r in faulty),
                fault_recall=sum(r['flag'] for r in faulty)/len(faulty) if faulty else None,
                decision_weighted_recall=sum(r['decision_change']*r['flag'] for r in faulty)/impact if impact else None,
                unflagged_decision_change=sum(r['decision_change'] for r in faulty if not r['flag']),
                total_decision_change=impact,
                unflagged_decision_mse=sum(r['decision_mse'] for r in faulty if not r['flag'])/len(faulty) if faulty else None,
                material_cases=len(material), material_recall=sum(r['flag'] for r in material)/len(material) if material else None,
                background_months=len(background), background_flags=sum(r['flag'] for r in background),
                background_burden=sum(r['flag'] for r in background)/len(background) if background else None)


def validation():
    OUT.mkdir(exist_ok=True); months,panel = load_source(); case_rows = cases('validation',months,panel)
    trials, exports = [], []
    for config in candidates():
        start=time.perf_counter(); rows=evaluate(config,case_rows,panel); score=metrics(rows)
        trials.append(config | score | {'elapsed_seconds':time.perf_counter()-start,'status':'success'}); exports += rows
    eligible=[r for r in trials if r['background_burden'] <= .05]
    assert eligible, 'Review budget infeasible; no relaxation authorized'
    winner=min(eligible,key=lambda r:(-r['decision_weighted_recall'],r['background_burden'],r['name']))
    write_csv('search.csv',trials);write_csv('validation_flags.csv',exports)
    write_json('validation_cases.json',case_rows)
    write_json('selection.json',dict(frozen_utc=datetime.now(timezone.utc).isoformat(),winner=winner['name'],baseline='structural',budget=.05,candidates=14,criterion='Max decision-weighted fault recall subject to unmodified burden<=5%, then lower burden and name',seed=SEED,test_cases_evaluated=False,source_kind='Retrospective measured public history with separate synthetic fault cases'))
    write_json('source_quality.json',dict(months=len(months),selected_categories=len(PRODUCTS),category_months=len(months)*len(PRODUCTS),validation_months=36,source_snapshots_sha256=hashlib.sha256((ROOT/'data/manifest.json').read_bytes()).hexdigest(),upstream_revision='888c9d819cec605b3839e223bad7492846a00fa0',source_reused=True,private_personal_data=False))
    print(json.dumps({'winner':winner['name'],'validation':{k:winner[k] for k in ['decision_weighted_recall','background_burden','fault_recall']},'candidates':len(trials)}))


def test():
    selection=json.loads((OUT/'selection.json').read_text());months,panel=load_source();case_rows=cases('test',months,panel)
    names=list(dict.fromkeys([selection['winner'],'structural','structural_replay']));records=[];summary=[];configs={r['name']:r for r in candidates()}
    for name in names:
        rows=evaluate(configs[name],case_rows,panel);records+=rows
        summary.append(dict(monitor=name,stratum='all',value='all',**metrics(rows)))
        for family in TEST_FAMILIES:summary.append(dict(monitor=name,stratum='family',value=family,**metrics([r for r in rows if r['family']==family])))
        for mechanism in ['validation_family','held_family']:summary.append(dict(monitor=name,stratum='mechanism',value=mechanism,**metrics([r for r in rows if r['mechanism_group']==mechanism])))
    selected={r['case_id']:r for r in records if r['monitor']==selection['winner'] and r['family']!='unmodified'}
    baseline={r['case_id']:r for r in records if r['monitor']=='structural' and r['family']!='unmodified'}
    blocks={}
    for key,row in selected.items():
        year,month=map(int,row['month'].split('-'));quarter=f'{year}Q{(month-1)//3+1}'
        block=blocks.setdefault(quarter,dict(quarter=quarter,impact=0,selected_detected_impact=0,baseline_detected_impact=0))
        block['impact']+=row['decision_change'];block['selected_detected_impact']+=row['decision_change']*row['flag'];block['baseline_detected_impact']+=row['decision_change']*baseline[key]['flag']
    blocks=[blocks[k] for k in sorted(blocks)];assert len(blocks)==10
    rng=np.random.default_rng(SEED);indices=rng.integers(0,len(blocks),size=(2000,len(blocks)));draws=[]
    for i,draw in enumerate(indices):
        denominator=sum(blocks[j]['impact'] for j in draw)
        gain=sum(blocks[j]['selected_detected_impact']-blocks[j]['baseline_detected_impact'] for j in draw)/denominator
        draws.append(dict(draw=i,quarter_indices=' '.join(str(x) for x in draw),weighted_recall_gain=gain))
    point=sum(b['selected_detected_impact']-b['baseline_detected_impact'] for b in blocks)/sum(b['impact'] for b in blocks)
    write_csv('test_flags.csv',records);write_csv('quarter_blocks.csv',blocks);write_csv('bootstrap_draws.csv',draws);write_json('test_cases.json',case_rows)
    write_json('test_summary.json',dict(evaluated_utc=datetime.now(timezone.utc).isoformat(),metrics=summary,bootstrap=dict(quantity='Selected minus structural-only decision-weighted recall',quarters=10,draws=2000,seed=SEED,mean=point,percentile95=np.quantile([r['weighted_recall_gain'] for r in draws],[.025,.975]).tolist(),scope='Conditional on measured source months and six explicit synthetic mechanisms; not observed production fault prevalence')))
    print(json.dumps({'test_months':30,'fault_cases':180,'monitors':names,'bootstrap_gain':point}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['validation','test']);phase=parser.parse_args().phase
    globals()[phase]()
