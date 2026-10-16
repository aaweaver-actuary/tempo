"""Task-owned offline container acceptance and serial benchmarks, using installed client."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import statistics
import subprocess
import time
import uuid

from tempo_analysis_jobs_contracts import v1
from tempo_stockfish_client import StockfishClient, WorkerSessionLost, stockfish_input_digest

ROOT = Path(__file__).resolve().parent
TASK = "analysis-b-stockfish"


def docker(*arguments, check=True, timeout=40):
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"docker {arguments[0]} failed: {result.stderr[:1000]}")
    return result


class Qualification:
    def __init__(self, image):
        image_metadata=json.loads(docker("image","inspect",image).stdout)[0]
        assert image_metadata["Os"] == "linux" and image_metadata["Architecture"] == "arm64", "Only Linux ARM64 is qualified"
        self.image = image_metadata["Id"]
        self.run_id = str(uuid.uuid4())
        self.containers = []
        self.records = []
        self.cases = []
        self.measurements = []
        self.counter = 0

    def command(self):
        name = "tempo-stockfish-b-" + uuid.uuid4().hex[:12]
        self.containers.append(name)
        return name, ["docker", "run", "--pull=never", "--rm", "-i", "--name", name, "--label", f"tempo.task={TASK}", "--label", f"tempo.run={self.run_id}", "--network=none", "--cpus=1", "--memory=512m", "--memory-swap=512m", "--pids-limit=64", "--read-only", self.image, "serve"]

    def client(self, command):
        self.current_client = StockfishClient(command)
        return self.current_client

    def job(self, client, **changes):
        self.counter += 1
        payload = json.loads((ROOT / "fixtures/synthetic/job.json").read_text())["payload"]
        payload.update({"job_id": f"observed-{self.counter}", "max_depth": 8, "max_nodes": None, "search_moves": [], **changes})
        payload["engine_version"] = client.identity["engine_version"]
        payload["input_digest"] = stockfish_input_digest(payload)
        return v1.StockfishJob.model_validate(payload)

    async def send(self, client, job):
        self.records.append({"model": "StockfishJob", "payload": job.model_dump(mode="json")})
        await client.send_job(job)

    async def until(self, client, predicate, timeout=20):
        async def receive():
            async for value in client.records():
                self.records.append({"model": type(value).__name__, "payload": value.model_dump(mode="json")})
                if predicate(value):
                    return value
        return await asyncio.wait_for(receive(), timeout)

    async def result(self, client, job):
        value = await self.until(client, lambda item: item.job_id == job.job_id and item.execution_generation == job.execution_generation and (isinstance(item, v1.StockfishResult) or isinstance(item, v1.JobStatus) and item.status == "failed"))
        assert isinstance(value, v1.StockfishResult), value
        return value

    async def ready_after(self, client, old_pid):
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            ready = next((event for event in reversed(client.diagnostics) if event.get("event") == "ready" and event.get("pid") != old_pid), None)
            if ready:
                return ready
            await asyncio.sleep(.01)
        raise AssertionError("capacity did not recover")

    async def cancel_active(self, client, name, job):
        if not any(record["model"] == "JobStatus" and record["payload"]["job_id"] == job.job_id and record["payload"]["status"] == "running" for record in self.records):
            await self.until(client, lambda item: isinstance(item, v1.JobStatus) and item.job_id == job.job_id and item.status == "running")
        started = next(event for event in reversed(client.diagnostics) if event.get("event") == "execution_started" and event.get("job_id") == job.job_id)
        pid = started["pid"]
        def ticks():
            value = docker("exec", name, "cat", f"/proc/{pid}/stat").stdout
            fields = value[value.rindex(")") + 2:].split()
            return int(fields[11]) + int(fields[12])
        before = await asyncio.to_thread(ticks)
        await asyncio.sleep(.12)
        after = await asyncio.to_thread(ticks)
        assert after > before, "actual engine CPU did not advance"
        request = v1.CancellationRequest(contract_version=1, workspace_id=job.workspace_id, job_id=job.job_id, request_revision=job.request_revision, execution_generation=job.execution_generation, cancellation_id=str(uuid.uuid4()))
        self.records.append({"model": "CancellationRequest", "payload": request.model_dump(mode="json")})
        beginning = time.monotonic()
        await client.send_cancellation(request)
        ack = await self.until(client, lambda item: isinstance(item, v1.CancellationAcknowledgement) and item.cancellation_id == request.cancellation_id and item.status == "terminated")
        terminated_ms = (time.monotonic() - beginning) * 1000
        await self.ready_after(client, pid)
        recovery_ms = (time.monotonic() - beginning) * 1000
        gone = await asyncio.to_thread(docker, "exec", name, "test", "!", "-d", f"/proc/{pid}")
        assert gone.returncode == 0
        return {"cpu_ticks_before": before, "cpu_ticks_after": after, "termination_ms": terminated_ms, "capacity_recovery_ms": recovery_ms, "pid": pid, "ack": ack.model_dump(mode="json")}

    def passed(self, name, **details):
        self.cases.append({"name": name, "success": True, **details})

    async def acceptance(self):
        name, command = self.command()
        cold = time.monotonic()
        async with self.client(command) as client:
            cold_ms = (time.monotonic() - cold) * 1000
            configuration = json.loads(docker("inspect", name).stdout)[0]
            assert configuration["HostConfig"]["Memory"] == 512 * 1024 * 1024
            assert configuration["HostConfig"]["MemorySwap"] == 512 * 1024 * 1024
            assert configuration["HostConfig"]["NanoCpus"] == 1000000000
            assert configuration["HostConfig"]["NetworkMode"] == "none"
            assert configuration["Mounts"] == []
            assert configuration["HostConfig"]["ReadonlyRootfs"] is True
            files = json.loads(docker("exec", name, "node", "-e", "console.log(JSON.stringify(require('fs').readdirSync('/opt/stockfish')))" ).stdout)
            assert not set(files) & {"app", "backend", "services", "contracts", ".git"}
            self.passed("B_restricted_artifact", installed_files=files, configuration={key: configuration["HostConfig"][key] for key in ("Memory", "MemorySwap", "NanoCpus", "PidsLimit", "NetworkMode", "ReadonlyRootfs")}, cold_initialization_ms=cold_ms)
            cases = [
                {},
                {"moves": ["e2e4"], "search_moves": ["e7e5"]},
                {"multi_pv": 3, "search_moves": ["e2e4", "d2d4", "c2c4"]},
                {"fen": "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "search_moves": ["e1g1"], "max_depth": 4},
                {"fen": "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 10", "search_moves": ["e5d6"], "max_depth": 4},
                {"fen": "4k3/P7/8/8/8/8/8/4K3 w - - 0 1", "search_moves": ["a7a8q"], "max_depth": 4},
                {"fen": "7k/5Q2/6K1/8/8/8/8/8 w - - 0 1", "search_moves": ["f7f8"], "max_depth": 4},
                {"fen": "8/8/8/8/8/6k1/5q2/7K b - - 0 1", "search_moves": ["f2f1"], "max_depth": 4},
                {"max_depth": 20, "max_nodes": 10000},
            ]
            outputs = []
            for changes in cases:
                job = self.job(client, **changes)
                before = time.monotonic(); await self.send(client, job); result = await self.result(client, job)
                outputs.append(result)
                self.measurements.append({"kind": "search", "job_id": job.job_id, "end_to_end_ms": (time.monotonic()-before)*1000, "actual_depth": result.variations[0].depth, "request": job.model_dump(mode="json")})
            assert any(variation.score.kind == "mate" for result in outputs for variation in result.variations)
            self.passed("B_real_engine_smoke", searches=len(outputs), identity=client.identity)
            replay_job = job
            await self.send(client, replay_job); replay = await self.result(client, replay_job)
            assert replay == outputs[-1]
            self.passed("B_exact_replay", observed=True)
            active = self.job(client, max_depth=128, max_nodes=None, constraints={"max_wall_time_ms":10000,"max_memory_mb":512})
            await self.send(client, active)
            await self.until(client, lambda value: isinstance(value,v1.JobStatus) and value.job_id==active.job_id and value.status=="running")
            busy = self.job(client)
            await self.send(client,busy)
            busy_status = await self.until(client, lambda value:isinstance(value,v1.JobStatus) and value.job_id==busy.job_id and value.status=="failed")
            assert busy_status.reason == "worker_busy" and busy_status.retryable
            cancelled = await self.cancel_active(client, name, active)
            self.passed("B_cancel_during_search", **cancelled)
            await self.send(client,busy)
            assert await self.until(client,lambda value:isinstance(value,v1.JobStatus) and value.job_id==busy.job_id and value.status=="failed") == busy_status
            retried_busy = busy.model_copy(update={"execution_generation":2})
            await self.send(client,retried_busy); await self.result(client,retried_busy)
            self.passed("real_busy_backpressure", retry_advanced_generation=True)
            recovered = self.job(client); await self.send(client, recovered); await self.result(client, recovered)
            wall = self.job(client, max_depth=128, max_nodes=None, constraints={"max_wall_time_ms":40,"max_memory_mb":512})
            old_pid = next(event["pid"] for event in reversed(client.diagnostics) if event.get("event")=="ready")
            await self.send(client, wall)
            status = await self.until(client, lambda value:isinstance(value,v1.JobStatus) and value.job_id==wall.job_id and value.status=="failed")
            assert status.reason == "execution_timeout" and status.retryable
            wall_status = status
            await self.ready_after(client, old_pid)
            too_small = self.job(client, constraints={"max_wall_time_ms":1000,"max_memory_mb":256})
            await self.send(client, too_small)
            status = await self.until(client, lambda value:isinstance(value,v1.JobStatus) and value.job_id==too_small.job_id and value.status=="failed")
            assert status.reason=="memory_budget_unsupported" and not status.retryable
            # Deliberate task-owned memory pressure proves the kernel cap, with a real engine present.
            before_events = docker("exec",name,"cat","/sys/fs/cgroup/memory.events").stdout
            pressure = await asyncio.to_thread(docker,"exec",name,"node","-e","const memory=Buffer.alloc(768*1024*1024,1);setInterval(()=>process.stdout.write(String(memory[0])),1000)",check=False,timeout=15)
            after_events = docker("exec",name,"cat","/sys/fs/cgroup/memory.events").stdout
            parse_events=lambda text:dict((key,int(value)) for key,value in (line.split() for line in text.splitlines()))
            assert parse_events(after_events)["oom_kill"] > parse_events(before_events)["oom_kill"]
            self.passed("B_wall_memory_failure", timeout=wall_status.model_dump(mode="json"), pressure_exit=pressure.returncode, before=before_events, after=after_events, limitation="The pressure helper was OOM-killed; no engine-specific OOM cause is inferred.")
            # Finite fixture backlog uses the installed client; it is not a durable scheduler.
            spec=importlib.util.spec_from_file_location("fixture_backlog",ROOT/"backlog_driver.py")
            driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
            template=json.loads((ROOT/"fixtures/synthetic/job.json").read_text())["payload"]
            backlog=await driver.run_fixture(client,json.loads((ROOT/"fixtures/backlog.json").read_text()),template,self.records.append)
            self.passed("B_finite_backlog_driver",**backlog)
            memory_peak=int(docker("exec",name,"cat","/sys/fs/cgroup/memory.peak").stdout)
            self.measurements.append({"kind":"whole_worker_memory_peak","bytes":memory_peak,"includes_deliberate_pressure":True})
            self.last_identity=client.identity
        removed=docker("inspect",name,check=False).returncode != 0
        assert removed, "owned container survived EOF shutdown"
        assert client.process.returncode == 0
        self.passed("B_clean_shutdown", container_removed=removed, exit_status=client.process.returncode)
        name2, command2=self.command()
        async with self.client(command2) as client:
            retried=replay_job.model_copy(update={"execution_generation":replay_job.execution_generation+1})
            await self.send(client,retried); new_result=await self.result(client,retried)
            assert new_result.execution_generation != outputs[-1].execution_generation
        self.passed("B_process_restart", old_generation=outputs[-1].execution_generation,new_generation=new_result.execution_generation)

    async def benchmark(self):
        cold=[]; searches=[]; cancellations=[]; peaks=[]
        for cycle in range(5):
            name,command=self.command(); began=time.monotonic()
            async with self.client(command) as client:
                cold.append({"end_to_end_startup_ms":(time.monotonic()-began)*1000,"engine_initialization_ms":next(event["initialization_ms"] for event in client.diagnostics if event.get("event")=="ready")})
                for index in range(4):
                    changes=[{}, {"moves":["e2e4"]}, {"search_moves":["e2e4"]}, {"multi_pv":3}][index]
                    job=self.job(client,max_depth=12,**changes)
                    began=time.monotonic();await self.send(client,job);result=await self.result(client,job)
                    end_to_end_ms=(time.monotonic()-began)*1000
                    for _ in range(100):
                        metric=next((event for event in reversed(client.diagnostics) if event.get("event")=="execution_finished" and event.get("job_id")==job.job_id),{})
                        if metric: break
                        await asyncio.sleep(.001)
                    searches.append({"end_to_end_ms":end_to_end_ms,"actual_depth":result.variations[0].depth,"nodes":metric.get("nodes"),"engine_time_ms":metric.get("engine_time_ms"),"job":job.model_dump(mode="json")})
                active=self.job(client,max_depth=128,max_nodes=None,constraints={"max_wall_time_ms":10000,"max_memory_mb":512})
                await self.send(client,active);cancellations.append(await self.cancel_active(client,name,active))
                peaks.append(int(docker("exec",name,"cat","/sys/fs/cgroup/memory.peak").stdout))
                self.last_identity=client.identity
        self.measurements.append({"kind":"serial_benchmark","cold_initialization_ms":cold,"searches":searches,"cancellations":cancellations,"memory_peak_bytes":peaks,"sample_scope":"five isolated cold sessions, twenty depth-12 searches, five observed in-flight cancellations; no universal SLO"})
        self.passed("serial_benchmark", cold_starts=5,searches=20,cancellations=5)

    async def slow_consumer(self):
        # Put the unread pipe beside the server; Docker attach buffering cannot hide it.
        name,command=self.command()
        template=json.loads((ROOT/"fixtures/synthetic/job.json").read_text())["payload"]
        harness=r"""
const {spawn}=require('node:child_process');const fs=require('node:fs');
(async()=>{
 const {stockfishInputDigest}=await import('/opt/stockfish/wire.mjs');
 const template=JSON.parse(process.argv[1]),newline=String.fromCharCode(10);
 const child=spawn(process.execPath,['/opt/stockfish/cli.mjs','serve'],{stdio:['pipe','pipe','pipe']});
 let buffer='',sent=0,timedOut=false,feeding=false,feedTimer,stdoutBytes=0,enginePid=null,reported=false;
 const events=[];
 function finish(code){
  if(reported)return;reported=true;feeding=false;clearTimeout(feedTimer);clearTimeout(watchdog);
  const result={exit_status:code,timed_out:timedOut,sent,stdout_bytes:stdoutBytes,
   engine_gone:enginePid!==null&&!fs.existsSync('/proc/'+enginePid),
   peak_bytes:Number(fs.readFileSync('/sys/fs/cgroup/memory.peak','utf8')),
   protocol_errors:events.filter(event=>event.event==='protocol_error'),
   actual_searches:events.filter(event=>event.event==='execution_started').length};
  process.stdout.write(JSON.stringify(result)+newline,()=>process.exit(0));
 }
 const watchdog=setTimeout(()=>{timedOut=true;clearTimeout(feedTimer);child.kill('SIGKILL');setTimeout(()=>finish(null),1000);},38000);
 function feed(){
  if(child.exitCode!==null||!feeding)return;
  const job={...template,job_id:'slow-'+sent++,max_depth:4,max_nodes:null,search_moves:[]};
  job.input_digest=stockfishInputDigest(job);
  if(!child.stdin.write(JSON.stringify({model:'StockfishJob',payload:job})+newline))child.stdin.once('drain',()=>{feedTimer=setTimeout(feed,1);});
  else feedTimer=setTimeout(feed,1);
 }
 child.stdin.on('error',()=>{feeding=false;clearTimeout(feedTimer);});
 child.stderr.on('data',chunk=>{
  buffer+=chunk.toString();let end;
  while((end=buffer.indexOf(newline))>=0){
   const text=buffer.slice(0,end);buffer=buffer.slice(end+1);
   try{const event=JSON.parse(text);events.push(event);if(events.length>128)events.shift();
    if(event.event==='ready'&&!feeding){enginePid=event.pid;feeding=true;feed();}
   }catch{}
  }
 });
 // stdout deliberately remains paused until server exit.
 child.once('exit',code=>{
  feeding=false;clearTimeout(feedTimer);child.stdin.destroy();
  child.stdout.on('data',chunk=>{stdoutBytes+=chunk.length;});child.stdout.resume();
  child.once('close',()=>finish(code));setTimeout(()=>finish(code),1000);
 });
})();
"""
        arguments=["run", "--pull=never", "--rm", "--name", name, "--label",f"tempo.task={TASK}","--label",f"tempo.run={self.run_id}","--network=none","--cpus=1","--memory=512m","--memory-swap=512m","--pids-limit=64","--read-only","--entrypoint","node",self.image,"-e",harness,json.dumps(template)]
        result=await asyncio.to_thread(docker,*arguments,timeout=50)
        observed=json.loads(result.stdout)
        self.last_slow_consumer=observed
        assert not observed["timed_out"] and observed["exit_status"]==1
        assert observed["engine_gone"] and observed["stdout_bytes"]<=2*1024*1024+65536
        assert observed["actual_searches"]>0
        assert any(event.get("reason")=="output_stalled" for event in observed["protocol_errors"])
        self.passed("B_slow_consumer",**observed)

    async def signal_shutdown(self):
        name,command=self.command()
        async with self.client(command) as client:
            job=self.job(client,max_depth=128,max_nodes=None,constraints={"max_wall_time_ms":10000,"max_memory_mb":512})
            await self.send(client,job)
            await self.until(client,lambda value:isinstance(value,v1.JobStatus) and value.job_id==job.job_id and value.status=="running")
            docker("kill","--signal=TERM",name)
            try:
                await self.until(client,lambda _:False,timeout=5)
            except WorkerSessionLost:
                pass
        assert docker("inspect",name,check=False).returncode != 0
        self.passed("real_signal_shutdown",container_removed=True)

    def cleanup(self):
        removed=[]
        missing=lambda result:result.returncode and ("No such object" in result.stderr or "No such container" in result.stderr)
        for name in self.containers:
            state=docker("inspect",name,check=False)
            if missing(state): continue
            if state.returncode: raise RuntimeError("owned container inspection unavailable")
            labels=json.loads(state.stdout)[0]["Config"]["Labels"]
            if labels.get("tempo.run") != self.run_id or labels.get("tempo.task") != TASK:
                raise RuntimeError("container ownership changed")
            result=docker("rm","-f",name,check=False)
            if result.returncode:
                for _ in range(20):
                    state=docker("inspect",name,check=False,timeout=5)
                    if missing(state): break
                    time.sleep(.25)
                else: raise RuntimeError("owned container cleanup remains unconfirmed")
            removed.append(name)
        return {"removed_remaining_owned_containers":removed,"all_created_names":self.containers}


async def run(arguments):
    qualifier=Qualification(arguments.image)
    report={"complete":False,"image_id":qualifier.image,"platform":"linux/arm64","runtime":"Node 26.10.0","cpu_limit":1,"memory_limit_bytes":536870912,"swap":False}
    failure=None
    try:
        if arguments.benchmark: await qualifier.benchmark()
        else:
            await qualifier.acceptance()
            await qualifier.slow_consumer()
            await qualifier.signal_shutdown()
        exchange={"scope":"actual installed Stockfish output","records":qualifier.records}
        Path(arguments.exchange).write_text(json.dumps(exchange,indent=2)+"\n")
        spec=importlib.util.spec_from_file_location("independent_stockfish_consumer",ROOT/"tests/contracts/consume_observed.py")
        consumer=importlib.util.module_from_spec(spec);spec.loader.exec_module(consumer)
        report["independent_consumer"]=consumer.consume(exchange)
        qualifier.passed("B_installed_consumer",**report["independent_consumer"])
        report["complete"]=True
    except BaseException as error:
        failure=error; report["failure"]={"type":type(error).__name__,"message":str(error)[:2000]}
    finally:
        try:
            report["cleanup"]=qualifier.cleanup()
        except Exception as cleanup_error:
            report["cleanup"]={"failure":str(cleanup_error),"owned_names":qualifier.containers}
            report["complete"]=False
            if failure is None: failure=cleanup_error
        if qualifier.records:
            Path(arguments.exchange).write_text(json.dumps({"scope":"actual installed Stockfish output","records":qualifier.records},indent=2)+"\n")
        report["last_diagnostics"]=list(getattr(getattr(qualifier,"current_client",None),"diagnostics",[]))
        report["slow_consumer"]=getattr(qualifier,"last_slow_consumer",None)
        report["cases"]=qualifier.cases;report["measurements"]=qualifier.measurements
        report["identity"]=getattr(qualifier,"last_identity",None)
        Path(arguments.report).parent.mkdir(parents=True,exist_ok=True)
        Path(arguments.report).write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"complete":report["complete"],"passed":len(qualifier.cases),"report":arguments.report,"failure":report.get("failure")}))
    if failure: raise failure


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--image",required=True)
    parser.add_argument("--report",required=True)
    parser.add_argument("--exchange",required=True)
    parser.add_argument("--benchmark",action="store_true")
    asyncio.run(run(parser.parse_args()))
