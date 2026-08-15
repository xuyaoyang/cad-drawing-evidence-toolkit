using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;
using ACadSharp;
using ACadSharp.Entities;
using ACadSharp.IO;
using ACadSharp.Tables;
using CSMath;

internal static class ACadSharpPortableReader
{
    private const string SchemaVersion = "acadsharp-portable-evidence/0.1";
    private const string BackendVersion = "3.6.51";
    private const int MaxDepth = 32;

    private static readonly SortedDictionary<string, DiagnosticGroup> Notifications =
        new SortedDictionary<string, DiagnosticGroup>(StringComparer.Ordinal);
    private static readonly SortedDictionary<string, DiagnosticGroup> TraversalIssues =
        new SortedDictionary<string, DiagnosticGroup>(StringComparer.Ordinal);
    private static readonly SortedDictionary<string, long> RawEntityCounts =
        new SortedDictionary<string, long>(StringComparer.Ordinal);
    private static readonly SortedDictionary<string, long> ReachableEntityCounts =
        new SortedDictionary<string, long>(StringComparer.Ordinal);
    private static readonly SortedDictionary<string, long> UnsupportedReachableCounts =
        new SortedDictionary<string, long>(StringComparer.Ordinal);
    private static readonly List<Dictionary<string, object>> EvidenceRecords =
        new List<Dictionary<string, object>>();

    private static long _rawEntityCount;
    private static long _rawEntityWithHandleCount;
    private static long _rawAttributeCount;
    private static long _reachableInsertCount;
    private static long _nonUniformInsertCount;
    private static long _multipleInsertCount;
    private static long _evidenceMissingHandleCount;
    private static long _cycleStopCount;
    private static long _depthStopCount;

    private static int Main(string[] args)
    {
        string input;
        string output;
        string sourcePath;
        string sourceName;
        string expectedSha256;
        if (!TryParseArguments(args, out input, out output, out sourcePath, out sourceName, out expectedSha256))
        {
            Console.Error.WriteLine(
                "usage: ACadSharpPortableReader --input <copy.dwg> --output <evidence.json> " +
                "--source-path <original.dwg> --source-name <original-name.dwg> --source-sha256 <64-hex>");
            return 2;
        }

        try
        {
            input = Path.GetFullPath(input);
            output = Path.GetFullPath(output);
            sourcePath = Path.GetFullPath(sourcePath);
            if (!File.Exists(input))
                throw new FileNotFoundException("Input DWG copy was not found.", input);
            if (!File.Exists(sourcePath))
                throw new FileNotFoundException("Original source DWG was not found.", sourcePath);
            if (!String.Equals(Path.GetExtension(input), ".dwg", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Only DWG input is accepted by this candidate reader.");
            if (String.Equals(input, sourcePath, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("The parser input must be an analysis copy, not the original source path.");
            if (String.Equals(input, output, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("The evidence output cannot overwrite the DWG input.");
            if (!IsSha256(expectedSha256))
                throw new InvalidOperationException("--source-sha256 must be a 64-character hexadecimal SHA-256.");

            string copySha256 = GetSha256(input);
            if (!String.Equals(copySha256, expectedSha256, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("The analysis copy SHA-256 does not match the expected source SHA-256.");

            CadDocument document = DwgReader.Read(input, OnNotification);
            CollectRawCounts(document);
            CollectReachableEvidence(document);

            string status = GetStatus();
            string outputDirectory = Path.GetDirectoryName(output);
            if (!String.IsNullOrEmpty(outputDirectory))
                Directory.CreateDirectory(outputDirectory);
            WriteOutput(document, output, sourceName, copySha256, status);

            Console.WriteLine("status=" + status);
            Console.WriteLine("evidence_records=" + EvidenceRecords.Count.ToString(CultureInfo.InvariantCulture));
            Console.WriteLine("notifications=" + Notifications.Values.Sum(value => value.Count).ToString(CultureInfo.InvariantCulture));
            Console.WriteLine("output=" + output);
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex.GetType().FullName + ": " + ex.Message);
            return 1;
        }
    }

    private static bool TryParseArguments(
        string[] args,
        out string input,
        out string output,
        out string sourcePath,
        out string sourceName,
        out string expectedSha256)
    {
        input = null;
        output = null;
        sourcePath = null;
        sourceName = null;
        expectedSha256 = null;
        if (args.Length % 2 != 0)
            return false;
        for (int index = 0; index < args.Length; index += 2)
        {
            string name = args[index];
            string value = args[index + 1];
            if (name == "--input") input = value;
            else if (name == "--output") output = value;
            else if (name == "--source-path") sourcePath = value;
            else if (name == "--source-name") sourceName = value;
            else if (name == "--source-sha256") expectedSha256 = value;
            else return false;
        }
        return !String.IsNullOrWhiteSpace(input) &&
            !String.IsNullOrWhiteSpace(output) &&
            !String.IsNullOrWhiteSpace(sourcePath) &&
            !String.IsNullOrWhiteSpace(sourceName) &&
            !String.IsNullOrWhiteSpace(expectedSha256);
    }

    private static void CollectRawCounts(CadDocument document)
    {
        foreach (BlockRecord block in document.BlockRecords)
        {
            foreach (Entity entity in block.Entities)
            {
                _rawEntityCount++;
                if (entity.Handle > 0) _rawEntityWithHandleCount++;
                Increment(RawEntityCounts, EntityType(entity));
                Insert insert = entity as Insert;
                if (insert == null) continue;
                foreach (AttributeEntity attribute in insert.Attributes)
                {
                    _rawAttributeCount++;
                    Increment(RawEntityCounts, "ATTRIB");
                }
            }
        }
    }

    private static void CollectReachableEvidence(CadDocument document)
    {
        List<BlockRecord> roots = document.BlockRecords
            .Where(block => block.Layout != null)
            .OrderBy(block => block.Name, StringComparer.Ordinal)
            .ThenBy(block => block.Handle)
            .ToList();

        foreach (BlockRecord root in roots)
        {
            var blockStack = new HashSet<ulong>();
            if (root.Handle > 0) blockStack.Add(root.Handle);
            foreach (Entity entity in OrderedEntities(root.Entities))
                VisitEntity(entity, root.Name, root.Name, new List<string>(), new List<Transform>(), blockStack, 0);
        }
    }

    private static void VisitEntity(
        Entity entity,
        string rootSpace,
        string ownerBlock,
        List<string> blockPath,
        List<Transform> transforms,
        HashSet<ulong> blockStack,
        int depth)
    {
        string type = EntityType(entity);
        Increment(ReachableEntityCounts, type);

        Insert insert = entity as Insert;
        if (insert != null)
        {
            _reachableInsertCount++;
            if (!NearlyEqual(insert.XScale, insert.YScale) || !NearlyEqual(insert.XScale, insert.ZScale))
                _nonUniformInsertCount++;
            if (insert.IsMultiple || insert.RowCount > 1 || insert.ColumnCount > 1)
                _multipleInsertCount++;

            TryAddRecord(insert, "block-reference", rootSpace, ownerBlock, blockPath, transforms);
            if (insert.Block == null)
            {
                AddTraversalIssue("insert_block_missing", "Insert " + Handle(insert.Handle) + " has no resolved block definition.");
                return;
            }
            if (depth >= MaxDepth)
            {
                _depthStopCount++;
                AddTraversalIssue("maximum_recursion_depth", "Insert " + Handle(insert.Handle) + " exceeded recursion depth.");
                return;
            }
            if (insert.Block.Handle > 0 && blockStack.Contains(insert.Block.Handle))
            {
                _cycleStopCount++;
                AddTraversalIssue("cyclic_block_reference", "Insert " + Handle(insert.Handle) + " would revisit block " + Handle(insert.Block.Handle) + ".");
                return;
            }

            Transform transform;
            try
            {
                transform = insert.GetTransform();
            }
            catch (Exception ex)
            {
                AddTraversalIssue("insert_transform_failed", ex.GetType().Name + ": " + ex.Message);
                return;
            }

            var childTransforms = new List<Transform>();
            childTransforms.Add(transform);
            childTransforms.AddRange(transforms);
            var childPath = new List<string>(blockPath);
            childPath.Add(Handle(insert.Handle) + ":" + SafeName(insert.Block.Name));
            foreach (AttributeEntity attribute in insert.Attributes.OrderBy(item => item.Handle))
            {
                Increment(ReachableEntityCounts, "ATTRIB");
                // ACadSharp exposes ATTRIB positions in the owning space. Applying the
                // INSERT transform again produces a double transform on real drawings.
                TryAddRecord(attribute, "block-attribute", rootSpace, ownerBlock, childPath, transforms);
            }
            var childStack = new HashSet<ulong>(blockStack);
            if (insert.Block.Handle > 0) childStack.Add(insert.Block.Handle);
            foreach (Entity child in OrderedEntities(insert.Block.Entities))
            {
                VisitEntity(
                    child,
                    rootSpace,
                    insert.Block.Name,
                    childPath,
                    childTransforms,
                    childStack,
                    depth + 1);
            }
            return;
        }

        if (IsSupported(entity))
            TryAddRecord(entity, blockPath.Count == 0 ? "direct" : "block-definition", rootSpace, ownerBlock, blockPath, transforms);
        else
            Increment(UnsupportedReachableCounts, type);
    }

    private static void TryAddRecord(
        Entity source,
        string origin,
        string rootSpace,
        string ownerBlock,
        List<string> blockPath,
        List<Transform> transforms)
    {
        try
        {
            Entity entity = (Entity)source.Clone();
            foreach (Transform transform in transforms)
                entity.ApplyTransform(transform);
            EvidenceRecords.Add(CreateRecord(source, entity, origin, rootSpace, ownerBlock, blockPath));
        }
        catch (Exception ex)
        {
            AddTraversalIssue(
                "entity_transform_or_extract_failed:" + EntityType(source),
                Handle(source.Handle) + " | " + ex.GetType().Name + ": " + ex.Message);
        }
    }

    private static Dictionary<string, object> CreateRecord(
        Entity source,
        Entity transformed,
        string origin,
        string rootSpace,
        string ownerBlock,
        List<string> blockPath)
    {
        var record = new Dictionary<string, object>();
        record["entity_type"] = EntityType(source);
        record["origin"] = origin;
        record["handle"] = Handle(source.Handle);
        if (source.Handle == 0) _evidenceMissingHandleCount++;
        record["instance_key"] = rootSpace + "|" + String.Join("/", blockPath.ToArray()) + "|" + Handle(source.Handle);
        record["root_space"] = rootSpace;
        record["owner_block"] = ownerBlock;
        record["block_path"] = String.Join("/", blockPath.ToArray());
        record["layer"] = source.Layer == null ? "" : source.Layer.Name;
        record["is_invisible"] = source.IsInvisible;

        AttributeDefinition definition = transformed as AttributeDefinition;
        if (definition != null)
        {
            record["text"] = definition.Value ?? "";
            record["tag"] = definition.Tag ?? "";
            record["position"] = Point(definition.InsertPoint);
            record["rotation_radians"] = Finite(definition.Rotation);
            record["height"] = Finite(definition.Height);
            record["definition_template_not_placed_value"] = true;
            return record;
        }

        TextEntity text = transformed as TextEntity;
        if (text != null)
        {
            record["text"] = text.Value ?? "";
            record["position"] = Point(text.InsertPoint);
            record["rotation_radians"] = Finite(text.Rotation);
            record["height"] = Finite(text.Height);
            return record;
        }

        AttributeEntity attribute = transformed as AttributeEntity;
        if (attribute != null)
        {
            record["text"] = attribute.Value ?? "";
            record["tag"] = attribute.Tag ?? "";
            record["position"] = Point(attribute.InsertPoint);
            record["rotation_radians"] = Finite(attribute.Rotation);
            record["height"] = Finite(attribute.Height);
            return record;
        }

        MText mtext = transformed as MText;
        if (mtext != null)
        {
            record["text"] = mtext.Value ?? "";
            try { record["plain_text"] = mtext.PlainText ?? ""; }
            catch { record["plain_text"] = ""; }
            record["position"] = Point(mtext.InsertPoint);
            record["rotation_radians"] = Finite(mtext.Rotation);
            record["height"] = Finite(mtext.Height);
            record["rectangle_width"] = Finite(mtext.RectangleWidth);
            return record;
        }

        Insert insert = transformed as Insert;
        if (insert != null)
        {
            Insert originalInsert = (Insert)source;
            record["block_name"] = originalInsert.Block == null ? "" : originalInsert.Block.Name;
            record["block_handle"] = originalInsert.Block == null ? "" : Handle(originalInsert.Block.Handle);
            record["position"] = Point(insert.InsertPoint);
            record["rotation_radians"] = Finite(insert.Rotation);
            record["scale"] = new object[] { Finite(insert.XScale), Finite(insert.YScale), Finite(insert.ZScale) };
            record["attribute_count"] = originalInsert.Attributes.Count;
            record["is_multiple"] = originalInsert.IsMultiple;
            record["row_count"] = originalInsert.RowCount;
            record["column_count"] = originalInsert.ColumnCount;
            record["row_spacing"] = Finite(originalInsert.RowSpacing);
            record["column_spacing"] = Finite(originalInsert.ColumnSpacing);
            return record;
        }

        Line line = transformed as Line;
        if (line != null)
        {
            record["start"] = Point(line.StartPoint);
            record["end"] = Point(line.EndPoint);
            return record;
        }

        LwPolyline polyline = transformed as LwPolyline;
        if (polyline != null)
        {
            record["closed"] = polyline.IsClosed;
            var vertices = new List<Dictionary<string, object>>();
            foreach (LwPolyline.Vertex vertex in polyline.Vertices)
            {
                var value = new Dictionary<string, object>();
                value["x"] = Finite(vertex.Location.X);
                value["y"] = Finite(vertex.Location.Y);
                value["z"] = Finite(polyline.Elevation);
                value["bulge"] = Finite(vertex.Bulge);
                vertices.Add(value);
            }
            record["vertices"] = vertices;
            return record;
        }

        Arc arc = transformed as Arc;
        if (arc != null)
        {
            record["center"] = Point(arc.Center);
            record["radius"] = Finite(arc.Radius);
            record["start_angle_radians"] = Finite(arc.StartAngle);
            record["end_angle_radians"] = Finite(arc.EndAngle);
            return record;
        }

        Circle circle = transformed as Circle;
        if (circle != null)
        {
            record["center"] = Point(circle.Center);
            record["radius"] = Finite(circle.Radius);
            return record;
        }

        ACadSharp.Entities.Point point = transformed as ACadSharp.Entities.Point;
        if (point != null)
        {
            record["position"] = Point(point.Location);
            return record;
        }

        throw new InvalidOperationException("Unsupported record type reached serialization: " + transformed.GetType().FullName);
    }

    private static void WriteOutput(CadDocument document, string output, string sourceName, string copySha256, string status)
    {
        var serializer = new JavaScriptSerializer();
        serializer.MaxJsonLength = Int32.MaxValue;
        serializer.RecursionLimit = 128;

        var metadata = new Dictionary<string, object>();
        metadata["schema_version"] = SchemaVersion;
        metadata["backend"] = "ACadSharp";
        metadata["backend_version"] = BackendVersion;
        metadata["source_name"] = Path.GetFileName(sourceName);
        metadata["source_sha256"] = copySha256;
        metadata["dwg_version"] = document.Header.Version.ToString();
        metadata["status"] = status;
        metadata["formal_backend_equivalent"] = false;
        metadata["absence_proven"] = false;
        metadata["original_dwg_opened_by_parser"] = false;
        metadata["analysis_copy_only"] = true;
        metadata["world_coordinate_scope"] = "supported entities recursively reachable from model/paper-space block records";
        metadata["coordinate_evidence_status"] = "candidate_requires_field_comparison";
        metadata["attribute_coordinate_status"] = "parser_value_not_backend_equivalent";
        metadata["effective_layer_status"] = "not_implemented_unverified";
        metadata["layout_viewport_visibility_status"] = "not_implemented_unverified";

        var summary = new Dictionary<string, object>();
        summary["layer_count"] = document.Layers.Count;
        summary["layout_count"] = document.Layouts.Count();
        summary["block_record_count"] = document.BlockRecords.Count;
        summary["raw_entity_count_all_blocks"] = _rawEntityCount;
        summary["raw_entities_with_handle"] = _rawEntityWithHandleCount;
        summary["raw_insert_attribute_count"] = _rawAttributeCount;
        summary["reachable_insert_count"] = _reachableInsertCount;
        summary["evidence_record_count"] = EvidenceRecords.Count;
        summary["non_uniform_insert_count"] = _nonUniformInsertCount;
        summary["multiple_insert_count"] = _multipleInsertCount;
        summary["evidence_missing_handle_count"] = _evidenceMissingHandleCount;
        summary["cycle_stop_count"] = _cycleStopCount;
        summary["depth_stop_count"] = _depthStopCount;
        summary["notification_count"] = Notifications.Values.Sum(value => value.Count);
        summary["traversal_issue_count"] = TraversalIssues.Values.Sum(value => value.Count);
        summary["raw_entity_type_counts"] = CountList(RawEntityCounts);
        summary["reachable_entity_type_counts"] = CountList(ReachableEntityCounts);
        summary["unsupported_reachable_type_counts"] = CountList(UnsupportedReachableCounts);

        var limitations = new object[]
        {
            "candidate backend only; not equivalent to ZWCAD V5/V6/V7/V10/V13/V18",
            "layout viewport clipping, layer freeze and visibility are not evaluated",
            "dynamic-block effective state, xref completeness and proxy internals are not proven",
            "effective layer inheritance through nested inserts is not evaluated",
            "ATTRIB coordinates are parser candidates and are not backend-equivalent until field comparison passes",
            "MINSERT row/column occurrences are not expanded",
            "non-uniformly scaled circular/arc geometry requires comparison before use",
            "a content-negative result never proves that the drawing lacks the target object"
        };

        using (var writer = new StreamWriter(output, false, new UTF8Encoding(false)))
        {
            writer.Write("{\n  \"metadata\": ");
            writer.Write(serializer.Serialize(metadata));
            writer.Write(",\n  \"summary\": ");
            writer.Write(serializer.Serialize(summary));
            writer.Write(",\n  \"notifications\": ");
            writer.Write(serializer.Serialize(DiagnosticList(Notifications)));
            writer.Write(",\n  \"traversal_issues\": ");
            writer.Write(serializer.Serialize(DiagnosticList(TraversalIssues)));
            writer.Write(",\n  \"limitations\": ");
            writer.Write(serializer.Serialize(limitations));
            writer.Write(",\n  \"evidence_records\": [\n");
            for (int index = 0; index < EvidenceRecords.Count; index++)
            {
                if (index > 0) writer.Write(",\n");
                writer.Write("    ");
                writer.Write(serializer.Serialize(EvidenceRecords[index]));
            }
            writer.Write("\n  ]\n}\n");
        }
    }

    private static string GetStatus()
    {
        if (Notifications.Count > 0 || TraversalIssues.Count > 0 || UnsupportedReachableCounts.Count > 0 || _nonUniformInsertCount > 0)
            return "portable_readonly_candidate_unresolved";
        return "portable_readonly_candidate_ready_for_comparison";
    }

    private static void OnNotification(object sender, NotificationEventArgs args)
    {
        string message = args == null ? "unknown notification" : (args.Message ?? "unknown notification");
        string group = GroupNotification(message);
        DiagnosticGroup value;
        if (!Notifications.TryGetValue(group, out value))
        {
            value = new DiagnosticGroup(group);
            Notifications[group] = value;
        }
        value.Add(message);
    }

    private static string GroupNotification(string message)
    {
        if (message.StartsWith("Unlisted object with DXF name ", StringComparison.Ordinal))
        {
            int end = message.IndexOf(" has been", StringComparison.Ordinal);
            return end > 0 ? message.Substring(0, end) : message;
        }
        if (message.StartsWith("Entity in SortEntitiesTable ", StringComparison.Ordinal))
            return "Entity in SortEntitiesTable not found";
        if (message.StartsWith("Entry not found ", StringComparison.Ordinal))
            return "Dictionary entry not found";
        return message;
    }

    private static void AddTraversalIssue(string group, string example)
    {
        DiagnosticGroup value;
        if (!TraversalIssues.TryGetValue(group, out value))
        {
            value = new DiagnosticGroup(group);
            TraversalIssues[group] = value;
        }
        value.Add(example);
    }

    private static List<Dictionary<string, object>> DiagnosticList(SortedDictionary<string, DiagnosticGroup> source)
    {
        var result = new List<Dictionary<string, object>>();
        foreach (DiagnosticGroup group in source.Values.OrderByDescending(value => value.Count).ThenBy(value => value.Name, StringComparer.Ordinal))
        {
            var item = new Dictionary<string, object>();
            item["group"] = group.Name;
            item["count"] = group.Count;
            item["examples"] = group.Examples.ToArray();
            result.Add(item);
        }
        return result;
    }

    private static List<Dictionary<string, object>> CountList(SortedDictionary<string, long> counts)
    {
        var result = new List<Dictionary<string, object>>();
        foreach (KeyValuePair<string, long> pair in counts.OrderByDescending(item => item.Value).ThenBy(item => item.Key, StringComparer.Ordinal))
        {
            var item = new Dictionary<string, object>();
            item["entity_type"] = pair.Key;
            item["count"] = pair.Value;
            result.Add(item);
        }
        return result;
    }

    private static IEnumerable<Entity> OrderedEntities(IEnumerable<Entity> entities)
    {
        return entities.OrderBy(entity => entity.Handle).ThenBy(entity => EntityType(entity), StringComparer.Ordinal);
    }

    private static bool IsSupported(Entity entity)
    {
        return entity is TextEntity || entity is AttributeEntity || entity is MText || entity is Line ||
            entity is LwPolyline || entity is Circle || entity is Arc || entity is ACadSharp.Entities.Point;
    }

    private static string EntityType(Entity entity)
    {
        if (entity is AttributeDefinition) return "ATTDEF";
        if (entity is AttributeEntity) return "ATTRIB";
        if (entity is TextEntity) return "TEXT";
        if (entity is MText) return "MTEXT";
        if (entity is Insert) return "INSERT";
        if (entity is Line) return "LINE";
        if (entity is LwPolyline) return "LWPOLYLINE";
        if (entity is Arc) return "ARC";
        if (entity is Circle) return "CIRCLE";
        if (entity is ACadSharp.Entities.Point) return "POINT";
        return entity.GetType().Name.ToUpperInvariant();
    }

    private static object[] Point(XYZ value)
    {
        return new object[] { Finite(value.X), Finite(value.Y), Finite(value.Z) };
    }

    private static object Finite(double value)
    {
        if (Double.IsNaN(value) || Double.IsInfinity(value)) return null;
        return value;
    }

    private static bool NearlyEqual(double left, double right)
    {
        return Math.Abs(left - right) <= 1e-12 * Math.Max(1.0, Math.Max(Math.Abs(left), Math.Abs(right)));
    }

    private static string SafeName(string value)
    {
        return (value ?? "").Replace("/", "_").Replace("|", "_");
    }

    private static string Handle(ulong value)
    {
        return value == 0 ? "" : value.ToString("X", CultureInfo.InvariantCulture);
    }

    private static void Increment(SortedDictionary<string, long> values, string key)
    {
        long count;
        values.TryGetValue(key, out count);
        values[key] = count + 1;
    }

    private static bool IsSha256(string value)
    {
        if (String.IsNullOrEmpty(value) || value.Length != 64) return false;
        foreach (char character in value)
        {
            if (!Uri.IsHexDigit(character)) return false;
        }
        return true;
    }

    private static string GetSha256(string path)
    {
        using (FileStream stream = File.OpenRead(path))
        using (SHA256 algorithm = SHA256.Create())
        {
            return BitConverter.ToString(algorithm.ComputeHash(stream)).Replace("-", "");
        }
    }

    private sealed class DiagnosticGroup
    {
        public DiagnosticGroup(string name)
        {
            Name = name;
            Examples = new List<string>();
        }

        public string Name { get; private set; }
        public long Count { get; private set; }
        public List<string> Examples { get; private set; }

        public void Add(string example)
        {
            Count++;
            if (Examples.Count < 3 && !Examples.Contains(example)) Examples.Add(example);
        }
    }
}
