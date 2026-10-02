export default function ExpiredNotice() {
  return (
    <p className="border border-black p-2 text-sm">
      Your demo workspace expired after 24 hours and a fresh one was created. Your earlier cases are gone.{" "}
      <a href="/" className="underline">Back to the case list</a>
    </p>
  );
}
