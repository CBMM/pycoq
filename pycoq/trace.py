''' functions to trace system execve called of a specified executable '''

import os
import subprocess
import tempfile
import re
import ast


from typing import List, Dict, Optional

from dataclasses import dataclass, field

from strace_parser.json_transformer import to_json
from strace_parser.parser import get_parser


from pycoq.common import CoqContext, context_fname, dump_context

import pycoq.log



@dataclass
class ProcContext():
    executable: str = ''
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)


def hex_rep(b):
    if isinstance(b, str):
        return "".join(['\\' + hex(c)[1:]  for c in b.encode('utf8')])
    else:
        raise ValueError('in hex_rep on ' + str(b))


def dehex_str(s):
    if len(s) > 2 and s[0] == '"' and s[-1] =='"':
        try:
            temp = 'b' + s
            return ast.literal_eval(temp).decode('utf8')
        except Exception as exc:
            print("pycoq: ERROR DECODING", temp)
            raise exc
    else:
        return s


def dehex(d):
    if isinstance(d, str):
        return dehex_str(d)
    elif isinstance(d, list):
        return [dehex(e) for e in d]
    elif isinstance(d, dict):
        return {k:dehex(v) for k, v in d.items()}


def simplify(d):
    if isinstance(d, str):
        return d
    elif isinstance(d, list):
        return [simplify(e) for e in d]
    elif isinstance(d, dict):
        if d.keys() == {'type', 'value'} and d['type'] == 'other' or d['type'] == 'bracketed':
            return simplify(d['value'])
        else:
            return {simplify(k) : simplify(v) for k, v in d.items()}


def parse_strace_line(parser, line):
    line_tree = parser.parse(line)
    line_json = to_json(line_tree)
    assert isinstance(line_json, list)
    assert len(line_json) == 1
    d = line_json[0]
    if d['type'] == 'syscall' and d['name'] == 'execve':
        dargs = d['args']
        return simplify(dehex(dargs))


def dict_of_list(l, split='='):
    d = {}
    for e in l:
        assert isinstance(e, str)
        pos = e.find(split)
        assert pos > 0
        d[e[:pos]]  = e[pos+1:]
    return d


def record_context(line: str, parser, regex: str):
    '''
    creates and writes to a file a pycoq_context record for each
    argument matching regex in a call of executable
    '''

    record = parse_strace_line(parser, line)
    pycoq.log.debug(f"record {record}")
    p_context = ProcContext(executable=record[0], args=record[1], env=dict_of_list(record[2]))
    res = []
    for target in p_context.args:
        if re.compile(regex).fullmatch(target):
            pwd = p_context.env['PWD']
            coq_context = CoqContext(pwd=pwd,
                                     executable=p_context.executable,
                                     target=target,
                                     args=p_context.args,
                                     env=p_context.env)
            target_fname = os.path.join(pwd, target)
            res.append(dump_context(context_fname(target_fname), coq_context))
    return res


def parse_strace_logdir(logdir: str, executable: str, regex: str) -> List[str]:
    '''
    for each strace log file in logdir, for each
    strace record in log file, parse the record matching to executable calling regex
    and save the call information _pycoq_context
    '''

    pycoq.log.info(f"pycoq: parsing strace log "
                   f"execve({executable}) and recording"
                   f"arguments that match {regex} in cwd {os.getcwd()}")
    parser = get_parser()
    res = []
    lines = []
    for logfname_pid in os.listdir(logdir):
        with open(os.path.join(logdir,logfname_pid),'r') as log_file:
            for line in iter(log_file.readline, ''):
                lines.append(line)
    for line in lines:
        if line.find(hex_rep(executable)) != -1:
            pycoq.log.info(f"from {logdir} from {log_file} parsing..")
            res += record_context(line, parser, regex)
    return res


def strace_build(executable: str,
                 regex: str,
                 workdir: Optional[str],
                 command: List[str]) -> List[str]:

    ''' trace calls of executable during access to  files that match regex
    in workdir while executing the command and  returns the list of pycoq_context 
    file names
    '''

    with tempfile.TemporaryDirectory() as logdir:
        logfname = os.path.join(logdir, 'strace.log')
        pycoq.log.info(f"pycoq: tracing {executable} accesing {regex} while "
                       f"executing {command} from {workdir} with "
                       f"curdir {os.getcwd()}")
        with subprocess.Popen(['strace', '-e', 'trace=execve',
                               '-v','-ff','-s', '100000000',
                               '-xx','-ttt',
                               '-o', logfname] + command,
                              cwd=workdir,
                              text=True,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) as proc:
            for line in iter(proc.stdout.readline,''):
                pycoq.log.debug(f"strace stdout: {line}")
            pycoq.log.info(f"strace stderr: {proc.stderr.read()}"
                           "waiting strace to finish...")
            proc.wait()
        pycoq.log.info('strace finished')
        return parse_strace_logdir(logdir, executable, regex)

