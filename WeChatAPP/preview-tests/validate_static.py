#!/usr/bin/env python3
"""Offline structural checks only: not the WeChat WXML compiler or a device test."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/'miniprogram'
checks=[]
def check(condition,name):
    if not condition: raise AssertionError(name)
    checks.append(name)
def load(path):return json.loads(path.read_text())

def main():
    app=load(APP/'app.json')
    project=load(ROOT/'project.config.json')
    check(project['miniprogramRoot']=='miniprogram/','import root is native miniprogram')
    check(project['appid']=='touristappid','test-only AppID placeholder')
    check('libVersion' not in project,'no guessed library version pin')
    check(project['setting']['urlCheck'] is True,'URL checks remain enabled')
    check(len(app['pages'])==6 and len(set(app['pages']))==6,'six unique registered pages')
    check(set(p['pagePath'] for p in app['tabBar']['list']).issubset(set(app['pages'])),'tab pages are registered')
    check(not any(k in app for k in ('permission','requiredPrivateInfos','plugins')),'no account, private-data, or plugin permissions')
    check(load(APP/'sitemap.json')['rules']==[{'action':'disallow','page':'*'}],'preview excluded from search indexing')
    for p in app['pages']:
        for ext in ('.js','.json','.wxml','.wxss'):check((APP/(p+ext)).is_file(),'page exists: '+p+ext)
    for path in ROOT.rglob('*.json'):
        load(path)
    checks.append('all JSON files parse')
    js_files=list(APP.rglob('*.js'))
    for path in js_files:
        subprocess.run(['node','--check',str(path)],check=True,capture_output=True)
        source=path.read_text()
        for ref in re.findall(r"require\(['\"]([^'\"]+)['\"]\)",source):
            target=(path.parent/ref).resolve()
            check(target.is_relative_to(APP.resolve()),'require stays in client: '+str(path.relative_to(ROOT)))
            check(target.with_suffix('.js').is_file() or target.with_suffix('.json').is_file(),'require exists: '+ref)
    checks.append('all client JavaScript parses under Node (not WeChat runtime)')
    builtin={'view','text','button','input','switch','image','block'}
    wxml_files=list(APP.rglob('*.wxml'))
    for path in wxml_files:
        source=path.read_text()
        # Adapt WXML boolean else and namespace solely for XML structural parsing.
        xml=re.sub(r'\bwx:else(?=\s|>)','wx:else=""',source)
        xml=xml.replace('&','&amp;')
        tree=ET.fromstring('<root xmlns:wx="urn:wx">'+xml+'</root>')
        config=load(path.with_suffix('.json'))
        components=config.get('usingComponents',{})
        for key,ref in components.items():
            target=APP/ref.lstrip('/') if ref.startswith('/') else path.parent/ref
            for ext in ('.js','.json','.wxml','.wxss'):check(target.with_suffix(ext).is_file(),'component target exists: '+key+ext)
        javascript=path.with_suffix('.js').read_text()
        for node in tree.iter():
            if node.tag=='root':continue
            check(node.tag in builtin or node.tag in components,'known WXML tag: '+node.tag)
            for attr,value in node.attrib.items():
                if attr.startswith('bind') or attr.startswith('catch'):
                    check(bool(re.search(r'\b'+re.escape(value)+r'\s*\(',javascript)),'WXML event handler exists: '+value)
                if attr=='src':check(not re.match(r'^(https?:)?//',value),'no remote image src')
                if attr=='open-type':check(False,'no external action buttons')
        check('br' not in {e.tag for e in tree.iter()},'no HTML-only br element')
    checks.append('WXML tags balanced with registered components and handlers')
    module=(APP/'data/catalog.js').read_text()
    data=json.loads(module.split('module.exports = ',1)[1].removesuffix(';\n'))
    forbidden={'price','prices','sales','rank','recommendation','original_text','description','token','cookie','password','secret','authorization_header','image_review','candidate','candidate_status','review_method','profile_id'}
    def walk(value):
        if isinstance(value,dict):
            for key,item in value.items():
                check(key.lower() not in forbidden,'allowlisted public data key: '+key)
                walk(item)
        elif isinstance(value,list):
            for item in value:walk(item)
        elif isinstance(value,str):
            check(not re.search(r'/workspace/|/home/|Bearer\s|gh[pousr]_|github_pat_',value),'no private path or credential-shaped value')
    walk(data)
    manifest=load(APP/'data/asset-manifest.json')
    seen=set()
    for item in manifest['assets']:
        path=APP/item['path'].lstrip('/')
        check(path.resolve().is_relative_to((APP/'assets/reviewed').resolve()),'asset confined to reviewed local directory')
        check(path.is_file(),'asset file exists')
        check(hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256'],'asset sha preserved')
        check(path.stat().st_size==item['bytes'],'asset byte length preserved')
        seen.add(item['path'])
    refs=set()
    for product in data['products']:
        if product['imagePath']:refs.add(product['imagePath'])
        for evidence in product['evidence']:refs.add(evidence['path'])
        for field in list(product['fields'].values())+list(product['measurements'].values())+product['barcodes']:
            refs.update(field.get('evidence_paths',[]))
    check(refs==seen,'all image references exactly match reviewed manifest')
    actual={'/'+str(p.relative_to(APP)) for p in (APP/'assets/reviewed').iterdir() if p.is_file()}
    check(actual==seen,'no undocumented packaged image files')
    runtime='\n'.join(p.read_text() for p in js_files if p.name!='catalog.js')
    api_calls=set(re.findall(r'\b(?:wx|api)\.([a-zA-Z0-9_]+)\s*\(',runtime))
    allowed={'navigateTo','navigateBack','switchTab','showToast','getStorageSync','setStorageSync'}
    check(api_calls<=allowed,'runtime only uses local storage and navigation APIs')
    check(not re.search(r'\b(fetch|XMLHttpRequest|WebSocket|eval)\s*\(',runtime),'no network or dynamic code runtime')
    check(not re.search(r'url\s*\(\s*[\'\"]?(?:https?:)?//','\n'.join(p.read_text() for p in APP.rglob('*.wxss'))),'no remote CSS resources')
    # Static WCAG contrast arithmetic for the known normal-size text pairs.
    # This does not replace native rendering or a screen-reader test.
    def luminance(color):
        channels=[int(color[i:i+2],16)/255 for i in (1,3,5)]
        linear=[v/12.92 if v<=0.04045 else ((v+0.055)/1.055)**2.4 for v in channels]
        return sum(a*b for a,b in zip(linear,(0.2126,0.7152,0.0722)))
    global_styles=(APP/'app.wxss').read_text()
    detail_styles=(APP/'pages/detail/detail.wxss').read_text()
    card_styles=(APP/'components/product-card/product-card.wxss').read_text()
    def css_color(source,selector,property_name='color'):
        rule=re.search(re.escape(selector)+r'\s*\{([^}]+)\}',source).group(1)
        return re.search(r'(?:^|;)\s*'+property_name+r':\s*(#[0-9a-fA-F]{6})',rule).group(1)
    paper=css_color(global_styles,'.card','background')
    contrast_pairs=[(css_color(detail_styles,'.field-note'),paper),(css_color(detail_styles,'.unknown'),paper),(css_color(card_styles,'.placeholder'),css_color(card_styles,'.picture','background')),(css_color(global_styles,'.subtitle'),css_color(global_styles,'page','background')),(css_color(card_styles,'.meta,.date'),css_color(card_styles,'.product','background')),(css_color(global_styles,'.footer'),css_color(global_styles,'page','background'))]
    ratios=[]
    for foreground,background in contrast_pairs:
        light,dark=sorted((luminance(foreground),luminance(background)),reverse=True)
        ratio=(light+0.05)/(dark+0.05)
        check(ratio>=4.5,'normal text static contrast >=4.5: '+foreground)
        ratios.append(round(ratio,3))
    global_styles=(APP/'app.wxss').read_text()
    chip=re.search(r'\.chip\s*\{([^}]+)\}',global_styles).group(1)
    check('max-width:calc(100% - 12rpx)' in chip and 'word-break:break-all' in chip,'long search history has width and wrap guards')
    source_line=re.search(r'\.source-line\s*\{([^}]+)\}',(APP/'pages/detail/detail.wxss').read_text()).group(1)
    check('word-break:break-all' in source_line,'ISO source time and URL can wrap')
    package_bytes=sum(p.stat().st_size for p in APP.rglob('*') if p.is_file())
    result={'status':'PASS','structuralAssertions':len(checks),'registeredPages':len(app['pages']),'wxmlFiles':len(wxml_files),'javascriptFiles':len(js_files),'reviewedAssets':len(manifest['assets']),'packagedBytes':package_bytes,'runtimeApis':sorted(api_calls),'normalTextContrastRatios':ratios,'wechatCompilerExecuted':False,'developerToolsExecuted':False,'physicalDeviceExecuted':False,'htmlMockScreenshotUsedAsDeviceEvidence':False}
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
