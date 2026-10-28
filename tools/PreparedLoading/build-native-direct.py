"""Build native DLLs with an existing MSVC/SDK toolchain, without installation or deployment.

Supply an exact native checkout (including version.generated.h). Every translation
unit is compiled; this intentionally avoids timestamp-only header cache reuse.
"""
import argparse
import concurrent.futures
import json
import os
import sys
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--native', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--msvc', type=Path, required=True, help='MSVC version directory containing bin, include and lib')
parser.add_argument('--sdk', type=Path, default=Path('C:/Program Files (x86)/Windows Kits/10'))
parser.add_argument('--sdk-version', default='10.0.17763.0')
parser.add_argument('--fixture', action='store_true')
parser.add_argument('--only-runtime', action='store_true', help='Development iteration only; requires a previous full build in the same output')
args = parser.parse_args()
repo, out, vc, sdk = (p.resolve() for p in (args.native, args.output, args.msvc, args.sdk))
out.mkdir(parents=True, exist_ok=True)
root = out
fixture = args.fixture
tools = vc / 'bin/Hostx64/x64'
version = args.sdk_version
if not (repo / 'version.generated.h').is_file():
    raise SystemExit('The exact generated version header is required.')

env = dict(os.environ)
env['TEMP'] = env['TMP'] = str(root / 'temp')
(root / 'temp').mkdir(exist_ok=True)
env['PATH'] = str(tools) + os.pathsep + env['PATH']
env['INCLUDE'] = ';'.join(map(str, [vc/'include', vc/'atlmfc/include'] + [sdk/'Include'/version/x for x in ['ucrt','shared','um','winrt']]))
env['LIB'] = ';'.join(map(str, [vc/'lib/x64', vc/'atlmfc/lib/x64', sdk/'Lib'/version/'ucrt/x64', sdk/'Lib'/version/'um/x64']))
ns = {'m':'http://schemas.microsoft.com/developer/msbuild/2003'}
common = ['/nologo','/c','/O2','/MT','/EHa','/std:c++14','/Zc:__cplusplus','/DUNICODE','/D_UNICODE','/DNDEBUG','/DWIN32','/D_WINDOWS','/D_CRT_SECURE_NO_WARNINGS','/D_CRT_SECURE_CPP_OVERLOAD_STANDARD_NAMES=1','/D_CRT_SECURE_CPP_OVERLOAD_STANDARD_NAMES_COUNT=1', '/DCRC32C_STATIC=1','/DPCRE2_STATIC','/DPCRE2_CODE_UNIT_WIDTH=8','/DMIGOTO_DX=11']
includes = [repo, repo/'HLSLDecompiler',repo/'DirectXTK/Inc',repo/'DirectXTK/Src',repo/'D3D_Shaders',repo/'pcre2',repo/'BinaryDecompiler',repo/'BinaryDecompiler/include',repo/'crc32c-hw-1.0.5/include']
common += ['/I'+str(p) for p in includes]
projects = ['BinaryDecompiler/BinaryDecompiler.vcxproj','DirectXTK/DirectXTK_Desktop_2017.vcxproj','DirectX11/DirectX11.vcxproj']
jobs=[]
objects=[]
for project in projects:
    path=repo/project
    tree=ET.parse(path)
    for node in tree.findall('.//m:ItemGroup/m:ClCompile',ns):
        if 'Include' not in node.attrib: continue
        pattern=node.attrib['Include'].replace('\\','/')
        sources=path.parent.glob(pattern) if '*' in pattern else [path.parent/pattern]
        for source in sources:
            src=source.resolve()
            obj=out/(str(src.relative_to(repo)).replace('\\','_').replace('/','_')+'.obj')
            if fixture and src.name in {'IniHandler.cpp', 'Runtime.cpp', 'CommandList.cpp'}:
                obj = out / ('fixture_' + obj.name)
            objects.append(obj)
            jobs.append((src,obj))

def compile_one(pair):
    src,obj=pair
    command=[str(tools/'cl.exe'),*common,*(['/DXXMI_MODLOADING_TESTS'] if obj.name.startswith('fixture_') else []), '/Fo'+str(obj),str(src)]
    result=subprocess.run(command,cwd=src.parent,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    (out/(obj.name+'.log')).write_bytes(result.stdout)
    return {'source':str(src.relative_to(repo)),'exit':result.returncode,'log':str(out/(obj.name+'.log'))}

with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    results=list(pool.map(compile_one, [job for job in jobs if not args.only_runtime or job[0].name == 'Runtime.cpp']))
failures=[r for r in results if r['exit']]
(out/('runtime-compile-results.json' if args.only_runtime else 'compile-results.json')).write_text(json.dumps(results,indent=2))
print(json.dumps({'compiled':len(results),'failed':failures},indent=2),flush=True)
if failures: raise SystemExit(1)

toolkit_objects = [str(p) for p in objects if p.name.startswith('DirectXTK_')]
subprocess.run([str(tools/'lib.exe'), '/nologo', '/OUT:'+str(out/'DirectXTK.lib'), *toolkit_objects], env=env, check=True)
resource=out/'DirectX11.res'
result=subprocess.run([str(sdk/'bin'/version/'x64/rc.exe'),'/nologo','/fo'+str(resource),str(repo/'DirectX11/DirectX11.rc')],cwd=repo/'DirectX11',env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
(out/'resource.log').write_bytes(result.stdout)
if result.returncode:
    print(result.stdout.decode(errors='replace')); raise SystemExit(result.returncode)
objects.append(resource)
libs=[str(repo/'Nektra/NktHookLib64.lib'),str(repo/'pcre2/pcre2-8-64.lib'),'XINPUT9_1_0.lib','d3dcompiler.lib','DXGI.lib','Shlwapi.lib','Dbghelp.lib','windowscodecs.lib','ole32.lib','user32.lib','gdi32.lib','advapi32.lib','shell32.lib','uuid.lib','winmm.lib','Psapi.lib','runtimeobject.lib','propsys.lib']
command=[str(tools/'link.exe'),'/nologo','/DLL','/MACHINE:X64','/OPT:REF','/OPT:ICF','/OUT:'+str(out/('fixture.dll' if fixture else 'd3d11.dll')),'/DEF:'+str(repo/'DirectX11/d3d11Wrapper.def'),*map(str,objects),*libs]
result=subprocess.run(command,cwd=repo/'DirectX11',env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
(out/'link.log').write_bytes(result.stdout)
print(result.stdout.decode(errors='replace'),flush=True)
raise SystemExit(result.returncode)
