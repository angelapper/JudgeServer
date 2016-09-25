# coding=utf-8
from __future__ import unicode_literals

import SocketServer
import hashlib
import json
import os
import socket
import shutil
from SimpleXMLRPCServer import SimpleXMLRPCServer, SimpleXMLRPCRequestHandler

import _judger
import psutil

from judge_client import JudgeClient
from compiler import Compiler
from config import JUDGER_WORKSPACE_BASE, TEST_CASE_DIR
from exception import SignatureVerificationFailed, CompileError, SPJCompileError
from utils import make_signature, check_signature


class InitSubmissionEnv(object):
    def __init__(self, judger_workspace, submission_id):
        self.path = os.path.join(judger_workspace, submission_id)

    def __enter__(self):
        os.mkdir(self.path)
        os.chmod(self.path, 0777)
        return self.path

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
        # shutil.rmtree(self.path, ignore_errors=True)


class JudgeServer(object):
    def _health_check(self):
        ver = _judger.VERSION
        return {"hostname": socket.gethostname(),
                "cpu": psutil.cpu_percent(),
                "cpu_core": psutil.cpu_count(),
                "memory": psutil.virtual_memory().percent,
                "judger_version": ((ver >> 16) & 0xff, (ver >> 8) & 0xff, ver & 0xff)}

    def pong(self):
        return make_signature(token=self._token, **self._health_check())

    @property
    def _token(self):
        t = os.getenv("judger_token")
        if not t:
            raise SignatureVerificationFailed("token not set")
        return hashlib.sha256(t).hexdigest()

    def judge(self, data, signature, timestamp):
        # check_signature(token=self._token, data=data, signature=signature, timestamp=timestamp)
        ret = {"code": None, "data": None}
        try:
            ret["data"] = self._judge(**json.loads(data))
        except (CompileError, SPJCompileError, SignatureVerificationFailed) as e:
            ret["code"] = e.__class__.__name__
            ret["data"] = e.message
        except Exception as e:
            import traceback
            traceback.print_exc()
            ret["code"] = "ServerError"
            ret["data"] = ": ".join([e.__class__.__name__, e.message])
        return make_signature(token=self._token, **ret)

    def _judge(self, language_config, submission_id, src, max_cpu_time, max_memory, test_case_id):
        # init
        compile_config = language_config["compile"]
        spj_compile_config = language_config.get("spj_compile")

        with InitSubmissionEnv(JUDGER_WORKSPACE_BASE, submission_id=submission_id) as submission_dir:
            src_path = os.path.join(submission_dir, compile_config["src_name"])

            # write source code into file
            with open(src_path, "w") as f:
                f.write(src.encode("utf-8"))

            # compile source code, return exe file path
            exe_path = Compiler().compile(compile_config=compile_config,
                                          src_path=src_path,
                                          output_dir=submission_dir)

            judge_client = JudgeClient(run_config=language_config["run"],
                                       exe_path=exe_path,
                                       max_cpu_time=max_cpu_time,
                                       max_memory=max_memory,
                                       test_case_id=test_case_id)
            return judge_client.run()

            if spj_compile_config:
                spj_compile_config["src_name"].format(spj_version=spj_compile_config["version"])
                spj_compile_config["exe_name"].format(spj_version=spj_compile_config["version"])

                spj_src_path = os.path.join(TEST_CASE_DIR, test_case_id, spj_compile_config["src_name"])

                # if spj source code not found, then write it into file
                if not os.path.exists(spj_src_path):
                    with open(spj_src_path, "w") as f:
                        f.write(spj_compile_config["src"].encode("utf-8"))

                spj_exe_path = os.path.join(TEST_CASE_DIR, test_case_id, spj_compile_config["exe_name"])

                # if spj exe file not found, then compile it
                if not os.path.exists(spj_exe_path):
                    try:
                        spj_exe_path = Compiler().compile(compile_config=spj_compile_config,
                                                          src_path=spj_src_path,
                                                          output_dir=os.path.join(TEST_CASE_DIR, test_case_id))
                    # turn common CompileError into SPJCompileError
                    except CompileError as e:
                        raise SPJCompileError(e.message)
            return exe_path


class RPCServer(SocketServer.ThreadingMixIn, SimpleXMLRPCServer):
    pass


if not os.environ.get("judger_token"):
    print "judger_token not set"
    exit(-1)

server = RPCServer(('0.0.0.0', 8080), SimpleXMLRPCRequestHandler, allow_none=True)
server.register_instance(JudgeServer())
server.serve_forever()
