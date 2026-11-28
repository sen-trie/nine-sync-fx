"""Ordered execution operations extending the shared technique/draw selection.

Unlike a conversion-time draw inventory, execution retains clears, target
switches and concrete loop values in their original order.
"""
from dataclasses import dataclass

from .errors import FxUnsupported
from .techniques import FxDraw,fx_pick_technique,fx_technique_script,fx_pass_script,fx_global_int,fx_named_passes


@dataclass(frozen=True)
class FxOperation:
    kind: str
    value: object = None
    draw: object = None
    loop_values: tuple = ()
    pass_name: str = ""


def _commands(script):
    result=[]
    for command in script.split(';'):
        if not command.strip(): continue
        if '=' not in command: raise FxUnsupported(f'Invalid effect command {command!r}.')
        key,value=command.split('=',1)
        result.append((key.strip().lower(),value.strip()))
    return result


def _loops(commands):
    root=[];stack=[root]
    for key,value in commands:
        if key=='loopbycount':
            body=[];stack[-1].append(('loop',(value,body)));stack.append(body)
        elif key=='loopend':
            if len(stack)==1: raise FxUnsupported('Effect script has LoopEnd without LoopByCount.')
            stack.pop()
        else:stack[-1].append((key,value))
    if len(stack)!=1:raise FxUnsupported('Effect script has an unterminated LoopByCount.')
    return root


def fx_plan_operations(program,subset=0,values=None,pass_types=('object','object_ss'),technique=None):
    """Yield concrete operations for one subset, sharing FxDraw and selection.

    Effect state and loop variables persist in script order. A loop does not
    implicitly restore targets or a loop-index global changed by an inner loop.
    """
    technique=technique or fx_pick_technique(program,subset,pass_types)
    if technique is None:return
    # D3DX GetPassByName resolves the first declaration. Original Grass has
    # repeated height1/height2 names with different shaders, so last-wins is wrong.
    passes=fx_named_passes(technique)
    script=fx_technique_script(technique,program)
    commands=_loops(_commands(script)) if script is not None else [('pass',p.name) for p in technique.passes]
    context=dict(target='',order=0,loops=[],overrides={})

    def draw(fx_pass,kind):
        planned=FxDraw(technique,fx_pass,subset,list(context['loops']),context['target'],kind,context['order'])
        context['order']+=1
        return FxOperation('draw',draw=planned,loop_values=tuple(context['overrides'].items()))

    def run_pass(current,iteration):
        yield FxOperation('states',current.states,pass_name=current.name)
        pass_script=fx_pass_script(current,program)
        if pass_script is None:yield draw(current,'geometry')
        else:yield from walk(_loops(_commands(pass_script)),iteration,current)

    def walk(sequence,iteration=None,fx_pass=None):
        for key,value in sequence:
            if key=='loop':
                name,body=value
                count=fx_global_int(program,name,values)
                if count<0:count=0
                for index in range(count):
                    context['loops'].append(('',count,name))
                    yield from walk(body,index,fx_pass)
                    context['loops'].pop()
            elif key=='loopgetindex':
                if iteration is None:raise FxUnsupported('LoopGetIndex needs an executing loop.')
                if value not in program.globals:raise FxUnsupported(f'Unknown LoopGetIndex global {value!r}.')
                context['overrides'][value]=iteration
            elif key=='pass':
                if fx_pass is not None:raise FxUnsupported('A pass script cannot recursively execute another pass.')
                if value not in passes:raise FxUnsupported(f'Unknown effect pass {value!r}.')
                yield from run_pass(passes[value],iteration)
            elif key=='draw':
                if fx_pass is None or value.lower() not in ('geometry','buffer'):
                    raise FxUnsupported(f'Invalid Draw={value} command.')
                yield draw(fx_pass,value.lower())
            elif key.startswith('rendercolortarget'):
                suffix=key[len('rendercolortarget'):]
                if suffix and not suffix.isdigit():raise FxUnsupported(f'Invalid color-target binding {key!r}.')
                slot=int(suffix or 0)
                if not 0<=slot<4:raise FxUnsupported('D3D9 execution supports color target slots 0 through 3.')
                if slot==0:context['target']=value
                yield FxOperation('color_target',(slot,value))
            elif key=='renderdepthstenciltarget':yield FxOperation('depth_target',value)
            elif key in ('clearsetcolor','clearsetdepth'):yield FxOperation(key,value)
            elif key=='clear':
                if value.lower() not in ('color','depth'):raise FxUnsupported(f'Unimplemented clear {value!r}.')
                yield FxOperation('clear_'+value.lower())
            elif key=='scriptexternal':yield FxOperation('external',value)
            else:raise FxUnsupported(f'Unimplemented effect execution command {key}={value}.')

    if script is None:
        # Implicit execution enumerates declarations, without name lookup.
        for current in technique.passes:yield from run_pass(current,None)
    else:yield from walk(commands)
