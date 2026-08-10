function normalize(id) {
  return String(id).trim().toLowerCase();
}

function internalOnly() {
  return 1;
}

module.exports = { normalize };
