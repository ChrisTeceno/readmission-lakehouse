// Parse the semantic model's TMDL with Microsoft's TmdlSerializer (the library Power BI Desktop
// and Fabric use), print what it found, and check that serializing it back gives the same files.
//
//     dotnet run --project scripts/tmdl_check -- powerbi/ContosoReadmissions.SemanticModel/definition
//
// Exits 1 if the folder does not parse, a measure has no description, or the round trip differs.
using Microsoft.AnalysisServices.Tabular;

var source = args[0];
var db = TmdlSerializer.DeserializeDatabaseFromFolder(source);
var model = db.Model;
var failures = 0;
Console.WriteLine($"compatibilityLevel {db.CompatibilityLevel}, culture {model.Culture}");

foreach (var table in model.Tables)
{
    var columns = table.Columns.Count(c => c.Type != ColumnType.RowNumber);
    var modes = string.Join(",", table.Partitions.Select(p => p.Mode));
    Console.WriteLine($"table {table.Name}: {columns} columns, {table.Measures.Count} measures, {modes}");
    foreach (var measure in table.Measures.Where(m => string.IsNullOrWhiteSpace(m.Description)))
    {
        Console.WriteLine($"  measure {measure.Name} has no description");
        failures++;
    }
}
foreach (SingleColumnRelationship r in model.Relationships)
    Console.WriteLine(
        $"relationship {r.FromTable.Name}[{r.FromColumn.Name}] {r.FromCardinality}:{r.ToCardinality} "
        + $"{r.ToTable.Name}[{r.ToColumn.Name}]");
foreach (var expression in model.Expressions)
    Console.WriteLine($"expression {expression.Name} ({expression.Kind})");

var copy = Path.Combine(Path.GetTempPath(), $"tmdl-roundtrip-{Guid.NewGuid():N}");
TmdlSerializer.SerializeDatabaseToFolder(db, copy);
foreach (var file in Directory.GetFiles(copy, "*.tmdl", SearchOption.AllDirectories))
{
    var relative = Path.GetRelativePath(copy, file);
    var original = Path.Combine(source, relative);
    if (!File.Exists(original) || File.ReadAllText(original) != File.ReadAllText(file))
    {
        Console.WriteLine($"round trip differs: {relative}");
        failures++;
    }
}
Directory.Delete(copy, true);

Console.WriteLine(failures == 0 ? "ok" : $"{failures} problem(s)");
return failures == 0 ? 0 : 1;
