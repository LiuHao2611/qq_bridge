const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");

const html = `<!DOCTYPE html><html><body><canvas id="c" width="800" height="600" tabindex="0"></canvas></body></html>`;
const dom = new JSDOM(html, { runScripts: "dangerously", resources: "usable" });
const window = dom.window;
const document = window.document;

// Load LiteGraph
const lgCode = fs.readFileSync(path.join(__dirname, "frontend/static/vendor/litegraph.min.js"), "utf-8");
const script = document.createElement("script");
script.textContent = lgCode;
document.head.appendChild(script);

const LiteGraph = window.LiteGraph;
console.log("LiteGraph exists:", !!LiteGraph);
console.log("LGraph:", !!LiteGraph.LGraph);
console.log("LGraphCanvas:", !!LiteGraph.LGraphCanvas);
console.log("createNode:", typeof LiteGraph.createNode);

function MyNode() {
  this.title = "TestNode";
  this.addInput("in", "*");
  this.addOutput("out", "*");
  this.addWidget("text", "theme", "hello", () => {});
  this.size = [200, 80];
}
MyNode.title = "TestNode";
LiteGraph.registerNodeType("test/my", MyNode);

const graph = new LiteGraph.LGraph();
const canvas = new LiteGraph.LGraphCanvas(document.getElementById("c"), graph);
const n = LiteGraph.createNode("test/my");
n.pos = [100, 100];
graph.add(n);
console.log("node added, inputs:", n.inputs.length, "outputs:", n.outputs.length, "widgets:", n.widgets ? n.widgets.length : 0);
console.log("canvas allow_interaction:", canvas.allow_interaction, "allow_dragcanvas:", canvas.allow_dragcanvas);

// Simulate mouse down on node and move
const c = document.getElementById("c");
const rect = { left: 0, top: 0, width: 800, height: 600 };
c.getBoundingClientRect = () => rect;

function ev(type, x, y) {
  const e = new window.MouseEvent(type, { clientX: x, clientY: y, bubbles: true });
  c.dispatchEvent(e);
}

ev("mousedown", 120, 120);
console.log("mouse down, canvas.dragging_canvas:", canvas.dragging_canvas, "node being dragged:", canvas.node_dragged ? canvas.node_dragged.title : "none");
ev("mousemove", 130, 130);
ev("mouseup", 130, 130);
console.log("after drag node pos:", n.pos);
console.log("OK");
