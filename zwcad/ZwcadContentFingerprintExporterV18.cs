using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using ZwSoft.ZwCAD.ApplicationServices;
using ZwSoft.ZwCAD.DatabaseServices;
using ZwSoft.ZwCAD.EditorInput;
using ZwSoft.ZwCAD.Geometry;
using ZwSoft.ZwCAD.Runtime;

namespace CadReadingExploration
{
    /// <summary>
    /// V18 read-only directory-content prefilter.
    ///
    /// This exporter deliberately does less work than the V5/V6/V7/V10/V13
    /// pipeline. It scans every block definition once and records compact,
    /// deduplicated text, layer, space, block-reference and entity-type
    /// fingerprints. It does not count dampers and does not claim that a
    /// drawing without keyword hits contains no dampers.
    /// </summary>
    public sealed class ContentFingerprintExporterV18
    {
        [CommandMethod("CADPREFILTEREXPORT18")]
        public void ExportContentFingerprint()
        {
            Document document = Application.DocumentManager.MdiActiveDocument;
            if (document == null) return;

            Editor editor = document.Editor;
            Database database = document.Database;
            var counters = new ExportCounters();
            var layers = new SortedSet<string>(StringComparer.OrdinalIgnoreCase);
            var spaces = new SortedSet<string>(StringComparer.OrdinalIgnoreCase);
            var entityTypes = new SortedDictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            var texts = new Dictionary<string, TextFingerprint>(StringComparer.Ordinal);
            var blocks = new Dictionary<string, BlockFingerprint>(StringComparer.OrdinalIgnoreCase);

            try
            {
                using (Transaction transaction = database.TransactionManager.StartTransaction())
                {
                    ReadLayers(transaction, database, layers, counters);
                    BlockTable table = (BlockTable)transaction.GetObject(
                        database.BlockTableId,
                        OpenMode.ForRead);
                    HashSet<ObjectId> reachableDefinitions =
                        BuildReachableDefinitionSet(transaction, table, counters);

                    foreach (ObjectId blockId in table)
                    {
                        try
                        {
                            BlockTableRecord record = transaction.GetObject(
                                blockId,
                                OpenMode.ForRead) as BlockTableRecord;
                            if (record == null) continue;

                            counters.BlockDefinitionCount++;
                            string spaceName = SafeName(record);
                            bool isLayout = record.IsLayout;
                            if (!isLayout && !reachableDefinitions.Contains(blockId))
                            {
                                counters.UnusedBlockDefinitionCount++;
                                continue;
                            }
                            if (!isLayout)
                                counters.ReachableBlockDefinitionCount++;
                            spaces.Add(spaceName);

                            if (record.IsFromExternalReference)
                            {
                                counters.ExternalReferenceDefinitionCount++;
                                continue;
                            }

                            foreach (ObjectId entityId in record)
                            {
                                try
                                {
                                    DBObject value = transaction.GetObject(
                                        entityId,
                                        OpenMode.ForRead);
                                    Entity entity = value as Entity;
                                    if (entity == null) continue;

                                    counters.EntityCount++;
                                    string typeName = entity.GetType().Name;
                                    Increment(entityTypes, typeName);
                                    if (typeName.IndexOf(
                                            "Proxy",
                                            StringComparison.OrdinalIgnoreCase) >= 0)
                                    {
                                        counters.ProxyEntityCount++;
                                    }

                                    string layer = SafeLayer(entity);
                                    if (!String.IsNullOrWhiteSpace(layer))
                                        layers.Add(layer);

                                    DBText dbText = entity as DBText;
                                    if (dbText != null)
                                    {
                                        AddText(
                                            texts,
                                            counters,
                                            "DBText",
                                            isLayout ? "layout-direct" : "block-definition",
                                            dbText.TextString,
                                            dbText.Position,
                                            layer,
                                            spaceName,
                                            isLayout ? String.Empty : spaceName,
                                            SafeHandle(dbText));
                                        continue;
                                    }

                                    MText mText = entity as MText;
                                    if (mText != null)
                                    {
                                        AddText(
                                            texts,
                                            counters,
                                            "MText",
                                            isLayout ? "layout-direct" : "block-definition",
                                            mText.Contents,
                                            mText.Location,
                                            layer,
                                            spaceName,
                                            isLayout ? String.Empty : spaceName,
                                            SafeHandle(mText));
                                        continue;
                                    }

                                    AttributeDefinition attributeDefinition =
                                        entity as AttributeDefinition;
                                    if (attributeDefinition != null)
                                    {
                                        AddText(
                                            texts,
                                            counters,
                                            "AttributeDefinition",
                                            isLayout ? "layout-attribute-definition" : "block-attribute-definition",
                                            attributeDefinition.TextString,
                                            attributeDefinition.Position,
                                            layer,
                                            spaceName,
                                            isLayout ? String.Empty : spaceName,
                                            SafeHandle(attributeDefinition));
                                        continue;
                                    }

                                    BlockReference reference = entity as BlockReference;
                                    if (reference == null) continue;
                                    ReadBlockReference(
                                        transaction,
                                        reference,
                                        isLayout,
                                        spaceName,
                                        layer,
                                        blocks,
                                        texts,
                                        counters);
                                }
                                catch (System.Exception)
                                {
                                    counters.SkippedObjectErrorCount++;
                                }
                            }
                        }
                        catch (System.Exception)
                        {
                            counters.SkippedObjectErrorCount++;
                        }
                    }
                    transaction.Commit();
                }

                string drawingPath = database.Filename;
                string drawingDirectory = String.IsNullOrWhiteSpace(drawingPath)
                    ? Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory)
                    : Path.GetDirectoryName(drawingPath);
                string outputDirectory = Path.GetFullPath(
                    Path.Combine(drawingDirectory, "..", "输出"));
                Directory.CreateDirectory(outputDirectory);
                string outputPath = Path.Combine(
                    outputDirectory,
                    Path.GetFileNameWithoutExtension(drawingPath)
                        + ".cad_content_fingerprint_v18.json");

                string json = ToJson(
                    drawingPath,
                    counters,
                    layers,
                    spaces,
                    entityTypes,
                    texts,
                    blocks);
                File.WriteAllText(outputPath, json, new UTF8Encoding(false));
                editor.WriteMessage(
                    "\nCADPREFILTEREXPORT18: {0} entities, {1} text occurrences ({2} unique), {3} block fingerprints, {4} proxy entities, {5} skipped objects.\n{6}",
                    counters.EntityCount,
                    counters.TextOccurrenceCount,
                    texts.Count,
                    blocks.Count,
                    counters.ProxyEntityCount,
                    counters.SkippedObjectErrorCount,
                    outputPath);
            }
            catch (System.Exception exception)
            {
                editor.WriteMessage(
                    "\nCADPREFILTEREXPORT18 failed: {0}",
                    exception.Message);
            }
        }

        private static HashSet<ObjectId> BuildReachableDefinitionSet(
            Transaction transaction,
            BlockTable table,
            ExportCounters counters)
        {
            var roots = new List<ObjectId>();
            var graph = new Dictionary<ObjectId, List<ObjectId>>();

            foreach (ObjectId blockId in table)
            {
                try
                {
                    BlockTableRecord record = transaction.GetObject(
                        blockId,
                        OpenMode.ForRead) as BlockTableRecord;
                    if (record == null || record.IsFromExternalReference) continue;

                    foreach (ObjectId entityId in record)
                    {
                        try
                        {
                            BlockReference reference = transaction.GetObject(
                                entityId,
                                OpenMode.ForRead) as BlockReference;
                            if (reference == null) continue;
                            ObjectId target = reference.BlockTableRecord;
                            if (target.IsNull || !target.IsValid) continue;
                            if (record.IsLayout)
                            {
                                roots.Add(target);
                            }
                            else
                            {
                                List<ObjectId> targets;
                                if (!graph.TryGetValue(blockId, out targets))
                                {
                                    targets = new List<ObjectId>();
                                    graph.Add(blockId, targets);
                                }
                                targets.Add(target);
                            }
                        }
                        catch (System.Exception)
                        {
                            counters.SkippedObjectErrorCount++;
                        }
                    }
                }
                catch (System.Exception)
                {
                    counters.SkippedObjectErrorCount++;
                }
            }

            var reachable = new HashSet<ObjectId>();
            var pending = new Queue<ObjectId>();
            foreach (ObjectId root in roots)
            {
                if (reachable.Add(root))
                    pending.Enqueue(root);
            }
            while (pending.Count > 0)
            {
                ObjectId current = pending.Dequeue();
                List<ObjectId> targets;
                if (!graph.TryGetValue(current, out targets)) continue;
                foreach (ObjectId target in targets)
                {
                    if (reachable.Add(target))
                        pending.Enqueue(target);
                }
            }
            return reachable;
        }

        private static void ReadLayers(
            Transaction transaction,
            Database database,
            SortedSet<string> layers,
            ExportCounters counters)
        {
            try
            {
                LayerTable table = transaction.GetObject(
                    database.LayerTableId,
                    OpenMode.ForRead) as LayerTable;
                if (table == null) return;
                foreach (ObjectId layerId in table)
                {
                    try
                    {
                        LayerTableRecord layer = transaction.GetObject(
                            layerId,
                            OpenMode.ForRead) as LayerTableRecord;
                        if (layer != null && !String.IsNullOrWhiteSpace(layer.Name))
                            layers.Add(layer.Name);
                    }
                    catch (System.Exception)
                    {
                        counters.SkippedObjectErrorCount++;
                    }
                }
            }
            catch (System.Exception)
            {
                counters.SkippedObjectErrorCount++;
            }
        }

        private static void ReadBlockReference(
            Transaction transaction,
            BlockReference reference,
            bool isLayout,
            string ownerSpace,
            string layer,
            Dictionary<string, BlockFingerprint> blocks,
            Dictionary<string, TextFingerprint> texts,
            ExportCounters counters)
        {
            string name = SafeBlockName(reference);
            string effectiveName = name;
            bool isDynamic = false;
            try
            {
                isDynamic = reference.IsDynamicBlock;
                if (isDynamic)
                {
                    BlockTableRecord dynamicDefinition = transaction.GetObject(
                        reference.DynamicBlockTableRecord,
                        OpenMode.ForRead) as BlockTableRecord;
                    if (
                        dynamicDefinition != null
                        && !String.IsNullOrWhiteSpace(dynamicDefinition.Name))
                    {
                        effectiveName = dynamicDefinition.Name;
                    }
                }
            }
            catch (System.Exception)
            {
                counters.SkippedObjectErrorCount++;
            }

            string key = name + "\u001f" + effectiveName + "\u001f" + layer;
            BlockFingerprint block;
            if (!blocks.TryGetValue(key, out block))
            {
                block = new BlockFingerprint(name, effectiveName, layer, isDynamic);
                blocks.Add(key, block);
            }
            if (isLayout)
            {
                block.LayoutReferenceCount++;
                counters.LayoutBlockReferenceCount++;
            }
            else
            {
                block.DefinitionReferenceCount++;
                counters.DefinitionBlockReferenceCount++;
            }

            foreach (ObjectId attributeId in reference.AttributeCollection)
            {
                try
                {
                    AttributeReference attribute = transaction.GetObject(
                        attributeId,
                        OpenMode.ForRead) as AttributeReference;
                    if (attribute == null) continue;
                    AddText(
                        texts,
                        counters,
                        "AttributeReference",
                        isLayout ? "layout-block-attribute" : "definition-block-attribute",
                        attribute.TextString,
                        attribute.Position,
                        SafeLayer(attribute),
                        ownerSpace,
                        effectiveName,
                        SafeHandle(attribute));
                }
                catch (System.Exception)
                {
                    counters.SkippedObjectErrorCount++;
                }
            }
        }

        private static void AddText(
            Dictionary<string, TextFingerprint> texts,
            ExportCounters counters,
            string entityType,
            string origin,
            string text,
            Point3d position,
            string layer,
            string space,
            string blockName,
            string handle)
        {
            if (String.IsNullOrWhiteSpace(text)) return;
            counters.TextOccurrenceCount++;
            string key = entityType + "\u001f" + origin + "\u001f"
                + text + "\u001f" + layer + "\u001f" + space + "\u001f" + blockName;
            TextFingerprint fingerprint;
            if (texts.TryGetValue(key, out fingerprint))
            {
                fingerprint.Count++;
                return;
            }
            texts.Add(
                key,
                new TextFingerprint(
                    entityType,
                    origin,
                    text,
                    position,
                    layer,
                    space,
                    blockName,
                    handle));
        }

        private static void Increment(
            SortedDictionary<string, int> values,
            string key)
        {
            int count;
            values.TryGetValue(key, out count);
            values[key] = count + 1;
        }

        private static string SafeName(BlockTableRecord record)
        {
            try { return record.Name ?? String.Empty; }
            catch (System.Exception) { return String.Empty; }
        }

        private static string SafeBlockName(BlockReference reference)
        {
            try { return reference.Name ?? String.Empty; }
            catch (System.Exception) { return String.Empty; }
        }

        private static string SafeLayer(Entity entity)
        {
            try { return entity.Layer ?? String.Empty; }
            catch (System.Exception) { return String.Empty; }
        }

        private static string SafeHandle(DBObject value)
        {
            try { return value.Handle.ToString(); }
            catch (System.Exception) { return String.Empty; }
        }

        private static string ToJson(
            string drawingPath,
            ExportCounters counters,
            SortedSet<string> layers,
            SortedSet<string> spaces,
            SortedDictionary<string, int> entityTypes,
            Dictionary<string, TextFingerprint> texts,
            Dictionary<string, BlockFingerprint> blocks)
        {
            var json = new StringBuilder();
            json.Append("{\n  \"drawing\": \"")
                .Append(Escape(drawingPath))
                .Append("\",");
            json.Append("\n  \"export_status\": \"success\",");
            json.Append("\n  \"scope\": \"compact read-only text/layer/block/entity fingerprint; not a quantity result\",");
            json.Append("\n  \"entity_count\": ")
                .Append(counters.EntityCount)
                .Append(',');
            json.Append("\n  \"block_definition_count\": ")
                .Append(counters.BlockDefinitionCount)
                .Append(',');
            json.Append("\n  \"reachable_block_definition_count\": ")
                .Append(counters.ReachableBlockDefinitionCount)
                .Append(',');
            json.Append("\n  \"unused_block_definition_count\": ")
                .Append(counters.UnusedBlockDefinitionCount)
                .Append(',');
            json.Append("\n  \"external_reference_definition_count\": ")
                .Append(counters.ExternalReferenceDefinitionCount)
                .Append(',');
            json.Append("\n  \"layout_block_reference_count\": ")
                .Append(counters.LayoutBlockReferenceCount)
                .Append(',');
            json.Append("\n  \"definition_block_reference_count\": ")
                .Append(counters.DefinitionBlockReferenceCount)
                .Append(',');
            json.Append("\n  \"text_occurrence_count\": ")
                .Append(counters.TextOccurrenceCount)
                .Append(',');
            json.Append("\n  \"unique_text_record_count\": ")
                .Append(texts.Count)
                .Append(',');
            json.Append("\n  \"proxy_entity_count\": ")
                .Append(counters.ProxyEntityCount)
                .Append(',');
            json.Append("\n  \"skipped_object_error_count\": ")
                .Append(counters.SkippedObjectErrorCount)
                .Append(',');

            json.Append("\n  \"layers\": ");
            AppendStringArray(json, layers);
            json.Append(",\n  \"spaces\": ");
            AppendStringArray(json, spaces);

            json.Append(",\n  \"entity_type_counts\": {");
            int typeIndex = 0;
            foreach (KeyValuePair<string, int> item in entityTypes)
            {
                if (typeIndex++ > 0) json.Append(',');
                json.Append("\n    \"")
                    .Append(Escape(item.Key))
                    .Append("\": ")
                    .Append(item.Value);
            }
            if (entityTypes.Count > 0) json.Append('\n');
            json.Append("  },");

            var orderedBlocks = new List<BlockFingerprint>(blocks.Values);
            orderedBlocks.Sort(delegate(BlockFingerprint left, BlockFingerprint right)
            {
                int compared = StringComparer.OrdinalIgnoreCase.Compare(
                    left.EffectiveName,
                    right.EffectiveName);
                if (compared != 0) return compared;
                return StringComparer.OrdinalIgnoreCase.Compare(left.Layer, right.Layer);
            });
            json.Append("\n  \"block_records\": [");
            for (int i = 0; i < orderedBlocks.Count; i++)
            {
                BlockFingerprint block = orderedBlocks[i];
                if (i > 0) json.Append(',');
                json.Append("\n    {\"name\": \"")
                    .Append(Escape(block.Name))
                    .Append("\", \"effective_name\": \"")
                    .Append(Escape(block.EffectiveName))
                    .Append("\", \"layer\": \"")
                    .Append(Escape(block.Layer))
                    .Append("\", \"is_dynamic\": ")
                    .Append(block.IsDynamic ? "true" : "false")
                    .Append(", \"layout_reference_count\": ")
                    .Append(block.LayoutReferenceCount)
                    .Append(", \"definition_reference_count\": ")
                    .Append(block.DefinitionReferenceCount)
                    .Append('}');
            }
            if (orderedBlocks.Count > 0) json.Append('\n');
            json.Append("  ],");

            var orderedTexts = new List<TextFingerprint>(texts.Values);
            orderedTexts.Sort(delegate(TextFingerprint left, TextFingerprint right)
            {
                int compared = StringComparer.Ordinal.Compare(left.Origin, right.Origin);
                if (compared != 0) return compared;
                compared = StringComparer.Ordinal.Compare(left.Space, right.Space);
                if (compared != 0) return compared;
                return StringComparer.Ordinal.Compare(left.Text, right.Text);
            });
            json.Append("\n  \"text_records\": [");
            for (int i = 0; i < orderedTexts.Count; i++)
            {
                TextFingerprint text = orderedTexts[i];
                if (i > 0) json.Append(',');
                json.Append("\n    {\"entity_type\": \"")
                    .Append(Escape(text.EntityType))
                    .Append("\", \"origin\": \"")
                    .Append(Escape(text.Origin))
                    .Append("\", \"text\": \"")
                    .Append(Escape(text.Text))
                    .Append("\", \"count\": ")
                    .Append(text.Count)
                    .Append(", \"x\": ")
                    .Append(Number(text.Position.X))
                    .Append(", \"y\": ")
                    .Append(Number(text.Position.Y))
                    .Append(", \"z\": ")
                    .Append(Number(text.Position.Z))
                    .Append(", \"layer\": \"")
                    .Append(Escape(text.Layer))
                    .Append("\", \"space\": \"")
                    .Append(Escape(text.Space))
                    .Append("\", \"block_name\": \"")
                    .Append(Escape(text.BlockName))
                    .Append("\", \"first_handle\": \"")
                    .Append(Escape(text.FirstHandle))
                    .Append("\"}");
            }
            if (orderedTexts.Count > 0) json.Append('\n');
            json.Append("  ]\n}\n");
            return json.ToString();
        }

        private static void AppendStringArray(
            StringBuilder json,
            IEnumerable<string> values)
        {
            json.Append('[');
            int index = 0;
            foreach (string value in values)
            {
                if (index++ > 0) json.Append(',');
                json.Append('"').Append(Escape(value)).Append('"');
            }
            json.Append(']');
        }

        private static string Number(double value)
        {
            return value.ToString("R", CultureInfo.InvariantCulture);
        }

        private static string Escape(string value)
        {
            if (value == null) return String.Empty;
            return value
                .Replace("\\", "\\\\")
                .Replace("\"", "\\\"")
                .Replace("\r", "\\r")
                .Replace("\n", "\\n")
                .Replace("\t", "\\t");
        }

        private sealed class ExportCounters
        {
            public int EntityCount;
            public int BlockDefinitionCount;
            public int ReachableBlockDefinitionCount;
            public int UnusedBlockDefinitionCount;
            public int ExternalReferenceDefinitionCount;
            public int LayoutBlockReferenceCount;
            public int DefinitionBlockReferenceCount;
            public int TextOccurrenceCount;
            public int ProxyEntityCount;
            public int SkippedObjectErrorCount;
        }

        private sealed class TextFingerprint
        {
            public TextFingerprint(
                string entityType,
                string origin,
                string text,
                Point3d position,
                string layer,
                string space,
                string blockName,
                string firstHandle)
            {
                EntityType = entityType;
                Origin = origin;
                Text = text;
                Position = position;
                Layer = layer;
                Space = space;
                BlockName = blockName;
                FirstHandle = firstHandle;
                Count = 1;
            }

            public string EntityType { get; private set; }
            public string Origin { get; private set; }
            public string Text { get; private set; }
            public Point3d Position { get; private set; }
            public string Layer { get; private set; }
            public string Space { get; private set; }
            public string BlockName { get; private set; }
            public string FirstHandle { get; private set; }
            public int Count { get; set; }
        }

        private sealed class BlockFingerprint
        {
            public BlockFingerprint(
                string name,
                string effectiveName,
                string layer,
                bool isDynamic)
            {
                Name = name;
                EffectiveName = effectiveName;
                Layer = layer;
                IsDynamic = isDynamic;
            }

            public string Name { get; private set; }
            public string EffectiveName { get; private set; }
            public string Layer { get; private set; }
            public bool IsDynamic { get; private set; }
            public int LayoutReferenceCount { get; set; }
            public int DefinitionReferenceCount { get; set; }
        }
    }
}
