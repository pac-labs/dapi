import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
const source=readFileSync(new URL('../../looprd/panel_assets/files.js',import.meta.url),'utf8');
test('terminal filenames remain literal POSIX shell arguments',()=>{
 const api=vm.runInNewContext(source+';({shellQuote,absoluteFileUrl})',{URL,location:{origin:'https://api.example.com'}});
 assert.equal(api.shellQuote("./a'b;$(whoami).txt"),"'./a'\\''b;$(whoami).txt'");
 assert.equal(api.absoluteFileUrl('/_looprd/files/download/id'),'https://api.example.com/_looprd/files/download/id');
});
