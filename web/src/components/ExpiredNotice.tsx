export default function ExpiredNotice() {
  return (
    <p className="border border-black p-2 text-sm">
      This case wasn't found. Demo workspaces are deleted after 24 hours, so yours may have expired and a fresh one was created.{" "}
      <a href="/" className="underline">Back to the case list</a>
    </p>
  );
}
