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
    /// Read-only V6 block-instance exporter.
    ///
    /// The important difference from CADTEXTEXPORT5 is that this exporter records
    /// each inserted block instance, including a composite instance key for nested
    /// references. A nested BlockReference handle belongs to the shared block
    /// definition and can therefore repeat under multiple root insertions; the
    /// instance_key (root/nested/nested...) is the unique counting key.
    /// </summary>
    public sealed class SymbolInstanceExporter
    {
        [CommandMethod("CADSYMBOLEXPORT6")]
        public void ExportSymbolInstances()
        {
            Document document = Application.DocumentManager.MdiActiveDocument;
            if (document == null) return;

            Editor editor = document.Editor;
            Database database = document.Database;
            var records = new List<InstanceRecord>();
            var counters = new ExportCounters();

            try
            {
                using (Transaction transaction = database.TransactionManager.StartTransaction())
                {
                    BlockTable table = (BlockTable)transaction.GetObject(database.BlockTableId, OpenMode.ForRead);
                    foreach (ObjectId blockId in table)
                    {
                        BlockTableRecord space = transaction.GetObject(blockId, OpenMode.ForRead) as BlockTableRecord;
                        if (space == null || !space.IsLayout || space.IsFromExternalReference) continue;

                        foreach (ObjectId entityId in space)
                        {
                            BlockReference reference = transaction.GetObject(entityId, OpenMode.ForRead) as BlockReference;
                            if (reference == null) continue;

                            counters.RootInstances++;
                            string rootHandle = reference.Handle.ToString();
                            ReadInstance(
                                transaction,
                                reference,
                                space.Name,
                                new List<Matrix3d>(),
                                String.Empty,
                                String.Empty,
                                rootHandle,
                                String.Empty,
                                new HashSet<ObjectId>(),
                                records,
                                counters);
                        }
                    }
                    transaction.Commit();
                }

                string drawingPath = database.Filename;
                string drawingDirectory = String.IsNullOrWhiteSpace(drawingPath)
                    ? Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory)
                    : Path.GetDirectoryName(drawingPath);
                string outputDirectory = Path.GetFullPath(Path.Combine(drawingDirectory, "..", "输出"));
                Directory.CreateDirectory(outputDirectory);
                string outputPath = Path.Combine(
                    outputDirectory,
                    Path.GetFileNameWithoutExtension(drawingPath) + ".cad_symbol_export_v6.json");

                File.WriteAllText(outputPath, ToJson(drawingPath, counters, records), new UTF8Encoding(false));
                editor.WriteMessage(
                    "\nCADSYMBOLEXPORT6: exported {0} block instances ({1} root, {2} nested); {3} bounds unavailable.\n{4}",
                    records.Count,
                    counters.RootInstances,
                    counters.NestedInstances,
                    counters.BoundsUnavailable,
                    outputPath);
            }
            catch (System.Exception exception)
            {
                editor.WriteMessage("\nCADSYMBOLEXPORT6 failed: {0}", exception.Message);
            }
        }

        private static void ReadInstance(
            Transaction transaction,
            BlockReference reference,
            string space,
            List<Matrix3d> parentTransforms,
            string parentInstanceKey,
            string parentEntityHandle,
            string rootHandle,
            string parentNamePath,
            HashSet<ObjectId> definitionStack,
            List<InstanceRecord> records,
            ExportCounters counters)
        {
            string entityHandle = reference.Handle.ToString();
            string instanceKey = String.IsNullOrEmpty(parentInstanceKey)
                ? entityHandle
                : parentInstanceKey + "/" + entityHandle;
            string blockName = SafeBlockName(reference);
            string namePath = String.IsNullOrEmpty(parentNamePath)
                ? blockName
                : parentNamePath + "/" + blockName;

            BlockTableRecord definition = null;
            try
            {
                definition = transaction.GetObject(reference.BlockTableRecord, OpenMode.ForRead) as BlockTableRecord;
            }
            catch (System.Exception)
            {
                definition = null;
            }

            Point3d worldInsertion = Transform(reference.Position, parentTransforms);
            BoundsData bounds;
            bool boundsValid = TryGetWorldBounds(reference, parentTransforms, out bounds);
            if (!boundsValid) counters.BoundsUnavailable++;

            var attributes = ReadAttributes(transaction, reference);
            var definitionTexts = new List<string>();
            string geometrySignature = String.Empty;
            int definitionEntityCount = 0;
            string definitionHandle = String.Empty;
            string effectiveName = blockName;
            bool isDynamic = false;

            if (definition != null)
            {
                definitionHandle = definition.Handle.ToString();
                definitionTexts = ReadDirectDefinitionTexts(transaction, definition);
                geometrySignature = BuildGeometrySignature(transaction, definition, out definitionEntityCount);
                TryGetDynamicName(transaction, reference, ref isDynamic, ref effectiveName);
            }

            records.Add(new InstanceRecord(
                instanceKey,
                entityHandle,
                definitionHandle,
                parentInstanceKey,
                parentEntityHandle,
                rootHandle,
                namePath,
                blockName,
                effectiveName,
                isDynamic,
                space,
                reference.Layer,
                worldInsertion,
                reference.Rotation,
                reference.ScaleFactors,
                boundsValid,
                bounds,
                attributes,
                definitionTexts,
                geometrySignature,
                definitionEntityCount));

            if (definition == null || definition.IsFromExternalReference) return;
            ObjectId definitionId = definition.ObjectId;
            if (definitionStack.Contains(definitionId))
            {
                counters.CyclicDefinitionsSkipped++;
                return;
            }

            definitionStack.Add(definitionId);
            try
            {
                var childParentTransforms = new List<Matrix3d>();
                childParentTransforms.Add(reference.BlockTransform);
                childParentTransforms.AddRange(parentTransforms);

                foreach (ObjectId entityId in definition)
                {
                    BlockReference nested = transaction.GetObject(entityId, OpenMode.ForRead) as BlockReference;
                    if (nested == null) continue;
                    counters.NestedInstances++;
                    ReadInstance(
                        transaction,
                        nested,
                        space,
                        childParentTransforms,
                        instanceKey,
                        entityHandle,
                        rootHandle,
                        namePath,
                        definitionStack,
                        records,
                        counters);
                }
            }
            finally
            {
                definitionStack.Remove(definitionId);
            }
        }

        private static string SafeBlockName(BlockReference reference)
        {
            try { return reference.Name ?? String.Empty; }
            catch (System.Exception) { return String.Empty; }
        }

        private static void TryGetDynamicName(
            Transaction transaction,
            BlockReference reference,
            ref bool isDynamic,
            ref string effectiveName)
        {
            try
            {
                isDynamic = reference.IsDynamicBlock;
                if (!isDynamic) return;
                BlockTableRecord dynamicDefinition = transaction.GetObject(
                    reference.DynamicBlockTableRecord, OpenMode.ForRead) as BlockTableRecord;
                if (dynamicDefinition != null && !String.IsNullOrWhiteSpace(dynamicDefinition.Name))
                    effectiveName = dynamicDefinition.Name;
            }
            catch (System.Exception)
            {
                isDynamic = false;
            }
        }

        private static List<string> ReadAttributes(Transaction transaction, BlockReference reference)
        {
            var values = new List<string>();
            foreach (ObjectId attributeId in reference.AttributeCollection)
            {
                AttributeReference attribute = transaction.GetObject(attributeId, OpenMode.ForRead) as AttributeReference;
                if (attribute == null || String.IsNullOrWhiteSpace(attribute.TextString)) continue;
                values.Add(attribute.Tag + "=" + attribute.TextString);
            }
            return values;
        }

        private static List<string> ReadDirectDefinitionTexts(
            Transaction transaction,
            BlockTableRecord definition)
        {
            var values = new List<string>();
            foreach (ObjectId entityId in definition)
            {
                Entity entity = transaction.GetObject(entityId, OpenMode.ForRead) as Entity;
                DBText text = entity as DBText;
                if (text != null && !String.IsNullOrWhiteSpace(text.TextString))
                {
                    values.Add(text.TextString);
                    continue;
                }

                MText mtext = entity as MText;
                if (mtext != null && !String.IsNullOrWhiteSpace(mtext.Contents))
                    values.Add(mtext.Contents);
            }
            return values;
        }

        private static string BuildGeometrySignature(
            Transaction transaction,
            BlockTableRecord definition,
            out int entityCount)
        {
            var counts = new SortedDictionary<string, int>(StringComparer.Ordinal);
            bool hasBounds = false;
            double minX = 0.0, minY = 0.0, maxX = 0.0, maxY = 0.0;
            entityCount = 0;

            foreach (ObjectId entityId in definition)
            {
                Entity entity = transaction.GetObject(entityId, OpenMode.ForRead) as Entity;
                if (entity == null) continue;
                entityCount++;
                string typeName = entity.GetType().Name;
                if (!counts.ContainsKey(typeName)) counts[typeName] = 0;
                counts[typeName]++;

                try
                {
                    Extents3d extents = entity.GeometricExtents;
                    if (!hasBounds)
                    {
                        minX = extents.MinPoint.X;
                        minY = extents.MinPoint.Y;
                        maxX = extents.MaxPoint.X;
                        maxY = extents.MaxPoint.Y;
                        hasBounds = true;
                    }
                    else
                    {
                        minX = Math.Min(minX, extents.MinPoint.X);
                        minY = Math.Min(minY, extents.MinPoint.Y);
                        maxX = Math.Max(maxX, extents.MaxPoint.X);
                        maxY = Math.Max(maxY, extents.MaxPoint.Y);
                    }
                }
                catch (System.Exception)
                {
                    // Some proxy or non-graphical entities do not expose extents.
                }
            }

            var signature = new StringBuilder();
            foreach (KeyValuePair<string, int> item in counts)
            {
                if (signature.Length > 0) signature.Append(';');
                signature.Append(item.Key).Append('=').Append(item.Value);
            }
            if (hasBounds)
            {
                signature.Append(";w=").Append(Quantize(maxX - minX));
                signature.Append(";h=").Append(Quantize(maxY - minY));
            }
            return signature.ToString();
        }

        private static string Quantize(double value)
        {
            if (Math.Abs(value) < 1e-9) return "0";
            double magnitude = Math.Pow(10.0, Math.Floor(Math.Log10(Math.Abs(value))) - 3.0);
            if (magnitude <= 0.0 || Double.IsInfinity(magnitude) || Double.IsNaN(magnitude))
                return Number(value);
            double rounded = Math.Round(value / magnitude) * magnitude;
            return rounded.ToString("0.####", CultureInfo.InvariantCulture);
        }

        private static bool TryGetWorldBounds(
            Entity entity,
            List<Matrix3d> parentTransforms,
            out BoundsData bounds)
        {
            bounds = new BoundsData();
            try
            {
                Extents3d extents = entity.GeometricExtents;
                var corners = new[]
                {
                    new Point3d(extents.MinPoint.X, extents.MinPoint.Y, extents.MinPoint.Z),
                    new Point3d(extents.MinPoint.X, extents.MaxPoint.Y, extents.MinPoint.Z),
                    new Point3d(extents.MaxPoint.X, extents.MinPoint.Y, extents.MaxPoint.Z),
                    new Point3d(extents.MaxPoint.X, extents.MaxPoint.Y, extents.MaxPoint.Z)
                };

                Point3d first = Transform(corners[0], parentTransforms);
                double minX = first.X, minY = first.Y, minZ = first.Z;
                double maxX = first.X, maxY = first.Y, maxZ = first.Z;
                for (int i = 1; i < corners.Length; i++)
                {
                    Point3d point = Transform(corners[i], parentTransforms);
                    minX = Math.Min(minX, point.X);
                    minY = Math.Min(minY, point.Y);
                    minZ = Math.Min(minZ, point.Z);
                    maxX = Math.Max(maxX, point.X);
                    maxY = Math.Max(maxY, point.Y);
                    maxZ = Math.Max(maxZ, point.Z);
                }
                bounds = new BoundsData(
                    new Point3d(minX, minY, minZ),
                    new Point3d(maxX, maxY, maxZ));
                return true;
            }
            catch (System.Exception)
            {
                return false;
            }
        }

        private static Point3d Transform(Point3d point, List<Matrix3d> transforms)
        {
            Point3d transformed = point;
            for (int i = 0; i < transforms.Count; i++)
                transformed = transformed.TransformBy(transforms[i]);
            return transformed;
        }

        private static string ToJson(
            string drawingPath,
            ExportCounters counters,
            List<InstanceRecord> records)
        {
            var json = new StringBuilder();
            json.Append("{\n  \"drawing\": \"").Append(Escape(drawingPath)).Append("\",");
            json.Append("\n  \"scope\": \"read-only root and recursively expanded nested block-reference instances\",");
            json.Append("\n  \"counting_key\": \"instance_key (a composite handle path for nested instances)\",");
            json.Append("\n  \"root_instance_count\": ").Append(counters.RootInstances).Append(',');
            json.Append("\n  \"nested_instance_count\": ").Append(counters.NestedInstances).Append(',');
            json.Append("\n  \"bounds_unavailable_count\": ").Append(counters.BoundsUnavailable).Append(',');
            json.Append("\n  \"cyclic_definition_skip_count\": ").Append(counters.CyclicDefinitionsSkipped).Append(',');
            json.Append("\n  \"instance_record_count\": ").Append(records.Count).Append(',');
            json.Append("\n  \"records\": [");
            for (int i = 0; i < records.Count; i++)
            {
                if (i > 0) json.Append(',');
                AppendRecord(json, records[i]);
            }
            json.Append("\n  ]\n}\n");
            return json.ToString();
        }

        private static void AppendRecord(StringBuilder json, InstanceRecord record)
        {
            json.Append("\n    {\"instance_key\": \"").Append(Escape(record.InstanceKey))
                .Append("\", \"instance_handle\": \"").Append(Escape(record.InstanceHandle))
                .Append("\", \"definition_handle\": \"").Append(Escape(record.DefinitionHandle))
                .Append("\", \"parent_instance_key\": \"").Append(Escape(record.ParentInstanceKey))
                .Append("\", \"parent_instance_handle\": \"").Append(Escape(record.ParentInstanceHandle))
                .Append("\", \"root_instance_handle\": \"").Append(Escape(record.RootInstanceHandle))
                .Append("\", \"instance_path\": \"").Append(Escape(record.InstanceKey))
                .Append("\", \"name_path\": \"").Append(Escape(record.NamePath))
                .Append("\", \"block_name\": \"").Append(Escape(record.BlockName))
                .Append("\", \"effective_name\": \"").Append(Escape(record.EffectiveName))
                .Append("\", \"is_dynamic\": ").Append(record.IsDynamic ? "true" : "false")
                .Append(", \"space\": \"").Append(Escape(record.Space))
                .Append("\", \"layer\": \"").Append(Escape(record.Layer))
                .Append("\", \"x\": ").Append(Number(record.Insertion.X))
                .Append(", \"y\": ").Append(Number(record.Insertion.Y))
                .Append(", \"z\": ").Append(Number(record.Insertion.Z))
                .Append(", \"local_rotation_radians\": ").Append(Number(record.Rotation))
                .Append(", \"scale_x\": ").Append(Number(record.Scale.X))
                .Append(", \"scale_y\": ").Append(Number(record.Scale.Y))
                .Append(", \"scale_z\": ").Append(Number(record.Scale.Z))
                .Append(", \"bounds_valid\": ").Append(record.BoundsValid ? "true" : "false");

            if (record.BoundsValid)
            {
                json.Append(", \"min_x\": ").Append(Number(record.Bounds.Min.X))
                    .Append(", \"min_y\": ").Append(Number(record.Bounds.Min.Y))
                    .Append(", \"min_z\": ").Append(Number(record.Bounds.Min.Z))
                    .Append(", \"max_x\": ").Append(Number(record.Bounds.Max.X))
                    .Append(", \"max_y\": ").Append(Number(record.Bounds.Max.Y))
                    .Append(", \"max_z\": ").Append(Number(record.Bounds.Max.Z));
            }
            else
            {
                json.Append(", \"min_x\": null, \"min_y\": null, \"min_z\": null")
                    .Append(", \"max_x\": null, \"max_y\": null, \"max_z\": null");
            }

            json.Append(", \"definition_entity_count\": ").Append(record.DefinitionEntityCount)
                .Append(", \"geometry_signature\": \"").Append(Escape(record.GeometrySignature))
                .Append("\", \"attributes\": ");
            AppendStringArray(json, record.Attributes);
            json.Append(", \"definition_texts\": ");
            AppendStringArray(json, record.DefinitionTexts);
            json.Append('}');
        }

        private static void AppendStringArray(StringBuilder json, List<string> values)
        {
            json.Append('[');
            for (int i = 0; i < values.Count; i++)
            {
                if (i > 0) json.Append(',');
                json.Append('"').Append(Escape(values[i])).Append('"');
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
            public int RootInstances;
            public int NestedInstances;
            public int BoundsUnavailable;
            public int CyclicDefinitionsSkipped;
        }

        private sealed class BoundsData
        {
            public BoundsData()
            {
                Min = Point3d.Origin;
                Max = Point3d.Origin;
            }

            public BoundsData(Point3d min, Point3d max)
            {
                Min = min;
                Max = max;
            }

            public Point3d Min { get; private set; }
            public Point3d Max { get; private set; }
        }

        private sealed class InstanceRecord
        {
            public InstanceRecord(
                string instanceKey,
                string instanceHandle,
                string definitionHandle,
                string parentInstanceKey,
                string parentInstanceHandle,
                string rootInstanceHandle,
                string namePath,
                string blockName,
                string effectiveName,
                bool isDynamic,
                string space,
                string layer,
                Point3d insertion,
                double rotation,
                Scale3d scale,
                bool boundsValid,
                BoundsData bounds,
                List<string> attributes,
                List<string> definitionTexts,
                string geometrySignature,
                int definitionEntityCount)
            {
                InstanceKey = instanceKey;
                InstanceHandle = instanceHandle;
                DefinitionHandle = definitionHandle;
                ParentInstanceKey = parentInstanceKey;
                ParentInstanceHandle = parentInstanceHandle;
                RootInstanceHandle = rootInstanceHandle;
                NamePath = namePath;
                BlockName = blockName;
                EffectiveName = effectiveName;
                IsDynamic = isDynamic;
                Space = space;
                Layer = layer;
                Insertion = insertion;
                Rotation = rotation;
                Scale = scale;
                BoundsValid = boundsValid;
                Bounds = bounds;
                Attributes = attributes;
                DefinitionTexts = definitionTexts;
                GeometrySignature = geometrySignature;
                DefinitionEntityCount = definitionEntityCount;
            }

            public string InstanceKey { get; private set; }
            public string InstanceHandle { get; private set; }
            public string DefinitionHandle { get; private set; }
            public string ParentInstanceKey { get; private set; }
            public string ParentInstanceHandle { get; private set; }
            public string RootInstanceHandle { get; private set; }
            public string NamePath { get; private set; }
            public string BlockName { get; private set; }
            public string EffectiveName { get; private set; }
            public bool IsDynamic { get; private set; }
            public string Space { get; private set; }
            public string Layer { get; private set; }
            public Point3d Insertion { get; private set; }
            public double Rotation { get; private set; }
            public Scale3d Scale { get; private set; }
            public bool BoundsValid { get; private set; }
            public BoundsData Bounds { get; private set; }
            public List<string> Attributes { get; private set; }
            public List<string> DefinitionTexts { get; private set; }
            public string GeometrySignature { get; private set; }
            public int DefinitionEntityCount { get; private set; }
        }
    }
}
