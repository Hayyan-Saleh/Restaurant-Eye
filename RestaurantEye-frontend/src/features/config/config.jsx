import ZoneAnnotator from "./ZoneAnnotator";

function Config() {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold">Camera Zone Configuration</h1>
      <ZoneAnnotator />
    </div>
  );
}

export default Config;