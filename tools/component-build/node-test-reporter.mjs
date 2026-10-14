import { Transform } from "node:stream";

// Keep errors inspectable: Error.message/stack are not enumerable JSON properties.
function serializeError(_key, value) {
  return value instanceof Error
    ? Object.fromEntries(Object.getOwnPropertyNames(value).map(name => [name, value[name]]))
    : value;
}
const nodeTestReporter = new Transform({
  writableObjectMode: true,
  transform(event, _encoding, callback) {
    if (["test:pass", "test:fail", "test:summary", "test:stdout", "test:stderr", "test:diagnostic"].includes(event.type)) {
      this.push(JSON.stringify(event, serializeError) + "\n");
    }
    callback();
  },
});

export default nodeTestReporter;
