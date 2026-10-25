import http from "node:http";
import https from "node:https";
import net from "node:net";
import tls from "node:tls";
import { syncBuiltinESMExports } from "node:module";

const blocked = () => {
  throw new Error("Metadata attempted network access");
};
globalThis.fetch = blocked;
http.request = http.get = https.request = https.get = blocked;
net.connect = net.createConnection = tls.connect = blocked;
net.Socket.prototype.connect = blocked;
syncBuiltinESMExports();
