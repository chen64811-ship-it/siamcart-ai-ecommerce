// Syntax-check JS files via Bun's full parser (node is broken on this host).
// Usage: bun check_js_syntax.mjs file1.js file2.js ...
import { readFileSync } from "fs";

const transpiler = new Bun.Transpiler({ loader: "js" });
let failed = false;

for (const f of process.argv.slice(2)) {
  try {
    const code = readFileSync(f, "utf8");
    transpiler.transformSync(code);
    console.log("OK   " + f);
  } catch (e) {
    failed = true;
    console.error("FAIL " + f + " :: " + (e.message || e));
  }
}
process.exit(failed ? 1 : 0);
