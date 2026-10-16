import { Transform } from 'node:stream';
const reporter = new Transform({ writableObjectMode: true, transform(event, encoding, done) {
  if (['test:pass','test:fail','test:summary','test:stdout','test:stderr'].includes(event.type)) this.push(JSON.stringify(event, (_key, value) => value instanceof Error ? Object.fromEntries(Object.getOwnPropertyNames(value).map(name => [name, value[name]])) : value) + '\n');
  done();
} });
export default reporter;
