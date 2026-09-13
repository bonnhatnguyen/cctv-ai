// Polyfill Blob.prototype.stream in jsdom for Node Response compatibility
if (typeof Blob !== 'undefined' && !Blob.prototype.stream) {
  Blob.prototype.stream = function () {
    const blob = this;
    return new ReadableStream({
      async start(controller) {
        const buf = await blob.arrayBuffer();
        controller.enqueue(new Uint8Array(buf));
        controller.close();
      },
    });
  };
}
