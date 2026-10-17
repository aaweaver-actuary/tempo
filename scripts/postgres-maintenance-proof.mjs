import assert from 'node:assert/strict';

export function allocatedNativeProofSource(scriptPath, proofName, limit) {
  return `import pathlib,runpy,sys
sys.path.insert(0,str(pathlib.Path(${JSON.stringify(scriptPath)}).parent))
quota,period=map(int,pathlib.Path('/sys/fs/cgroup/cpu.max').read_text().split())
assert quota/period==${limit.cpus}
assert int(pathlib.Path('/sys/fs/cgroup/memory.max').read_text())==${limit.memoryMiB*1024*1024}
print(${JSON.stringify('PASS '+proofName)},flush=True)
runpy.run_path(${JSON.stringify(scriptPath)},run_name='__main__')`;
}

export function verifyRestoredPostgresConsumers(run, compose, clients) {
  const result=run('docker',[...compose,'ps','--format','json'],{stdio:'pipe',encoding:'utf8'});
  const running=new Set(result.stdout.trim().split('\n').filter(Boolean)
    .map(line=>JSON.parse(line)).filter(container=>container.State==='running').map(container=>container.Service));
  for(const service of clients)assert(running.has(service),`${service} was not restored after the native maintenance proof`);
}
