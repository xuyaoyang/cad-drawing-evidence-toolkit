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
    /// Read-only oriented-text exporter.
    ///
    /// V5 exports world positions but not the transformed text axis. V7 keeps a
    /// composite block-instance path and transforms the DBText/MText direction
    /// vector through every parent BlockReference so layout markers can be
    /// classified by their actual WCS orientation.
    /// </summary>
    public sealed class OrientedTextExporterV7
    {
#if SAFE_COMMAND
        [CommandMethod("CADORIENTEDTEXTEXPORT7SAFE")]
#else
        [CommandMethod("CADORIENTEDTEXTEXPORT7")]
#endif
        public void ExportOrientedText()
        {
            Document document = Application.DocumentManager.MdiActiveDocument;
            if (document == null) return;

            Editor editor = document.Editor;
            Database database = document.Database;
            var records = new List<OrientedTextRecord>();
            var counters = new ExportCounters();

            try
            {
                using (Transaction transaction = database.TransactionManager.StartTransaction())
                {
                    BlockTable table = (BlockTable)transaction.GetObject(
                        database.BlockTableId, OpenMode.ForRead);
                    foreach (ObjectId blockId in table)
                    {
                        try
                        {
                            BlockTableRecord space = transaction.GetObject(
                                blockId, OpenMode.ForRead) as BlockTableRecord;
                            if (space == null || !space.IsLayout || space.IsFromExternalReference)
                                continue;

                            foreach (ObjectId entityId in space)
                            {
                                try
                                {
                                    Entity entity = transaction.GetObject(
                                        entityId, OpenMode.ForRead) as Entity;
                                    if (entity == null) continue;

                                    DBText text = entity as DBText;
                                    if (text != null)
                                    {
                                        AddDbText(
                                            records,
                                            "direct",
                                            "direct/" + text.Handle,
                                            String.Empty,
                                            text,
                                            space.Name,
                                            new List<Matrix3d>(),
                                            counters);
                                        counters.DirectText++;
                                        continue;
                                    }

                                    MText mtext = entity as MText;
                                    if (mtext != null)
                                    {
                                        AddMText(
                                            records,
                                            "direct",
                                            "direct/" + mtext.Handle,
                                            String.Empty,
                                            mtext,
                                            space.Name,
                                            new List<Matrix3d>(),
                                            counters);
                                        counters.DirectText++;
                                        continue;
                                    }

                                    BlockReference reference = entity as BlockReference;
                                    if (reference == null) continue;
                                    counters.RootBlockInstances++;
                                    ReadBlock(
                                        transaction,
                                        reference,
                                        space.Name,
                                        reference.Handle.ToString(),
                                        reference.Name,
                                        new List<Matrix3d> { reference.BlockTransform },
                                        new HashSet<ObjectId>(),
                                        records,
                                        counters);
                                }
                                catch (System.Exception)
                                {
                                    counters.SkippedObjectErrors++;
                                }
                            }
                        }
                        catch (System.Exception)
                        {
                            counters.SkippedObjectErrors++;
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
                        + ".cad_oriented_text_export_v7.json");

                File.WriteAllText(
                    outputPath,
                    ToJson(drawingPath, counters, records),
                    new UTF8Encoding(false));
                editor.WriteMessage(
                    "\nCADORIENTEDTEXTEXPORT7: exported {0} oriented text records "
                    + "({1} direct, {2} from block definitions); {3} bounds unavailable; "
                    + "{4} invalid/special objects skipped.\n{5}",
                    records.Count,
                    counters.DirectText,
                    counters.BlockDefinitionText,
                    counters.BoundsUnavailable,
                    counters.SkippedObjectErrors,
                    outputPath);
            }
            catch (System.Exception exception)
            {
                editor.WriteMessage(
                    "\nCADORIENTEDTEXTEXPORT7 failed: {0}", exception.Message);
            }
        }

        private static void ReadBlock(
            Transaction transaction,
            BlockReference reference,
            string space,
            string instancePath,
            string namePath,
            List<Matrix3d> transforms,
            HashSet<ObjectId> definitionStack,
            List<OrientedTextRecord> records,
            ExportCounters counters)
        {
            ObjectId definitionId = reference.BlockTableRecord;
            if (definitionId.IsNull || !definitionId.IsValid)
            {
                counters.SkippedObjectErrors++;
                return;
            }
            if (definitionStack.Contains(definitionId))
            {
                counters.CyclicDefinitionsSkipped++;
                return;
            }
            definitionStack.Add(definitionId);
            try
            {
                BlockTableRecord definition;
                try
                {
                    definition = transaction.GetObject(
                        definitionId, OpenMode.ForRead) as BlockTableRecord;
                }
                catch (System.Exception)
                {
                    counters.SkippedObjectErrors++;
                    return;
                }
                if (definition == null || definition.IsFromExternalReference) return;

                foreach (ObjectId entityId in definition)
                {
                    try
                    {
                        Entity entity = transaction.GetObject(
                            entityId, OpenMode.ForRead) as Entity;
                        if (entity == null) continue;

                        DBText text = entity as DBText;
                        if (text != null)
                        {
                            AddDbText(
                                records,
                                "block-definition",
                                instancePath + "/" + text.Handle,
                                namePath,
                                text,
                                space,
                                transforms,
                                counters);
                            counters.BlockDefinitionText++;
                            continue;
                        }

                        MText mtext = entity as MText;
                        if (mtext != null)
                        {
                            AddMText(
                                records,
                                "block-definition",
                                instancePath + "/" + mtext.Handle,
                                namePath,
                                mtext,
                                space,
                                transforms,
                                counters);
                            counters.BlockDefinitionText++;
                            continue;
                        }

                        BlockReference nested = entity as BlockReference;
                        if (nested == null) continue;
                        counters.NestedBlockInstances++;
                        var nestedTransforms = new List<Matrix3d>();
                        nestedTransforms.Add(nested.BlockTransform);
                        nestedTransforms.AddRange(transforms);
                        ReadBlock(
                            transaction,
                            nested,
                            space,
                            instancePath + "/" + nested.Handle,
                            namePath + "/" + nested.Name,
                            nestedTransforms,
                            definitionStack,
                            records,
                            counters);
                    }
                    catch (System.Exception)
                    {
                        counters.SkippedObjectErrors++;
                    }
                }
            }
            finally
            {
                definitionStack.Remove(definitionId);
            }
        }

        private static void AddDbText(
            List<OrientedTextRecord> records,
            string origin,
            string recordKey,
            string blockPath,
            DBText text,
            string space,
            List<Matrix3d> transforms,
            ExportCounters counters)
        {
            if (String.IsNullOrWhiteSpace(text.TextString)) return;
            Add(
                records,
                "DBText",
                origin,
                recordKey,
                blockPath,
                text.Handle.ToString(),
                text.TextString,
                text.Position,
                text.Rotation,
                text.Layer,
                space,
                text,
                transforms,
                counters);
        }

        private static void AddMText(
            List<OrientedTextRecord> records,
            string origin,
            string recordKey,
            string blockPath,
            MText text,
            string space,
            List<Matrix3d> transforms,
            ExportCounters counters)
        {
            if (String.IsNullOrWhiteSpace(text.Contents)) return;
            Add(
                records,
                "MText",
                origin,
                recordKey,
                blockPath,
                text.Handle.ToString(),
                text.Contents,
                text.Location,
                text.Rotation,
                text.Layer,
                space,
                text,
                transforms,
                counters);
        }

        private static void Add(
            List<OrientedTextRecord> records,
            string entityType,
            string origin,
            string recordKey,
            string blockPath,
            string handle,
            string text,
            Point3d localPosition,
            double localRotation,
            string layer,
            string space,
            Entity entity,
            List<Matrix3d> transforms,
            ExportCounters counters)
        {
            Point3d worldPosition = Transform(localPosition, transforms);
            Vector3d localAxis = new Vector3d(
                Math.Cos(localRotation), Math.Sin(localRotation), 0.0);
            Vector3d worldAxis = Transform(localAxis, transforms);
            double axisLength = Math.Sqrt(
                worldAxis.X * worldAxis.X + worldAxis.Y * worldAxis.Y);
            if (axisLength > 1e-12)
                worldAxis = new Vector3d(
                    worldAxis.X / axisLength, worldAxis.Y / axisLength, 0.0);
            double worldRotation = Math.Atan2(worldAxis.Y, worldAxis.X);

            BoundsData bounds;
            bool boundsValid = TryGetWorldBounds(entity, transforms, out bounds);
            if (!boundsValid) counters.BoundsUnavailable++;
            records.Add(new OrientedTextRecord(
                recordKey,
                entityType,
                origin,
                text,
                worldPosition,
                localRotation,
                worldRotation,
                worldAxis,
                layer,
                space,
                handle,
                blockPath,
                boundsValid,
                bounds));
        }

        private static Point3d Transform(
            Point3d point, List<Matrix3d> transforms)
        {
            Point3d transformed = point;
            for (int i = 0; i < transforms.Count; i++)
                transformed = transformed.TransformBy(transforms[i]);
            return transformed;
        }

        private static Vector3d Transform(
            Vector3d vector, List<Matrix3d> transforms)
        {
            Vector3d transformed = vector;
            for (int i = 0; i < transforms.Count; i++)
                transformed = transformed.TransformBy(transforms[i]);
            return transformed;
        }

        private static bool TryGetWorldBounds(
            Entity entity,
            List<Matrix3d> transforms,
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
                Point3d first = Transform(corners[0], transforms);
                double minX = first.X, minY = first.Y, minZ = first.Z;
                double maxX = first.X, maxY = first.Y, maxZ = first.Z;
                for (int i = 1; i < corners.Length; i++)
                {
                    Point3d point = Transform(corners[i], transforms);
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

        private static string ToJson(
            string drawingPath,
            ExportCounters counters,
            List<OrientedTextRecord> records)
        {
            var json = new StringBuilder();
            json.Append("{\n  \"drawing\": \"").Append(Escape(drawingPath)).Append("\",");
            json.Append("\n  \"scope\": \"read-only direct and recursively expanded DBText/MText with WCS axis\",");
            json.Append("\n  \"counting_key\": \"record_key = block instance path plus text handle\",");
            json.Append("\n  \"direct_text_count\": ").Append(counters.DirectText).Append(',');
            json.Append("\n  \"block_definition_text_count\": ").Append(counters.BlockDefinitionText).Append(',');
            json.Append("\n  \"root_block_instance_count\": ").Append(counters.RootBlockInstances).Append(',');
            json.Append("\n  \"nested_block_instance_count\": ").Append(counters.NestedBlockInstances).Append(',');
            json.Append("\n  \"bounds_unavailable_count\": ").Append(counters.BoundsUnavailable).Append(',');
            json.Append("\n  \"cyclic_definition_skip_count\": ").Append(counters.CyclicDefinitionsSkipped).Append(',');
            json.Append("\n  \"skipped_object_error_count\": ").Append(counters.SkippedObjectErrors).Append(',');
            json.Append("\n  \"text_record_count\": ").Append(records.Count).Append(',');
            json.Append("\n  \"records\": [");
            for (int i = 0; i < records.Count; i++)
            {
                if (i > 0) json.Append(',');
                AppendRecord(json, records[i]);
            }
            json.Append("\n  ]\n}\n");
            return json.ToString();
        }

        private static void AppendRecord(
            StringBuilder json, OrientedTextRecord record)
        {
            json.Append("\n    {\"record_key\": \"").Append(Escape(record.RecordKey))
                .Append("\", \"entity_type\": \"").Append(Escape(record.EntityType))
                .Append("\", \"origin\": \"").Append(Escape(record.Origin))
                .Append("\", \"text\": \"").Append(Escape(record.Text))
                .Append("\", \"x\": ").Append(Number(record.Position.X))
                .Append(", \"y\": ").Append(Number(record.Position.Y))
                .Append(", \"z\": ").Append(Number(record.Position.Z))
                .Append(", \"local_rotation_radians\": ").Append(Number(record.LocalRotation))
                .Append(", \"world_rotation_radians\": ").Append(Number(record.WorldRotation))
                .Append(", \"world_axis_x\": ").Append(Number(record.WorldAxis.X))
                .Append(", \"world_axis_y\": ").Append(Number(record.WorldAxis.Y))
                .Append(", \"layer\": \"").Append(Escape(record.Layer))
                .Append("\", \"space\": \"").Append(Escape(record.Space))
                .Append("\", \"handle\": \"").Append(Escape(record.Handle))
                .Append("\", \"block_path\": \"").Append(Escape(record.BlockPath))
                .Append("\", \"bounds_valid\": ").Append(record.BoundsValid ? "true" : "false");
            if (record.BoundsValid)
            {
                json.Append(", \"min_x\": ").Append(Number(record.Bounds.Min.X))
                    .Append(", \"min_y\": ").Append(Number(record.Bounds.Min.Y))
                    .Append(", \"max_x\": ").Append(Number(record.Bounds.Max.X))
                    .Append(", \"max_y\": ").Append(Number(record.Bounds.Max.Y));
            }
            else
            {
                json.Append(", \"min_x\": null, \"min_y\": null")
                    .Append(", \"max_x\": null, \"max_y\": null");
            }
            json.Append('}');
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
            public int DirectText;
            public int BlockDefinitionText;
            public int RootBlockInstances;
            public int NestedBlockInstances;
            public int BoundsUnavailable;
            public int CyclicDefinitionsSkipped;
            public int SkippedObjectErrors;
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

        private sealed class OrientedTextRecord
        {
            public OrientedTextRecord(
                string recordKey,
                string entityType,
                string origin,
                string text,
                Point3d position,
                double localRotation,
                double worldRotation,
                Vector3d worldAxis,
                string layer,
                string space,
                string handle,
                string blockPath,
                bool boundsValid,
                BoundsData bounds)
            {
                RecordKey = recordKey;
                EntityType = entityType;
                Origin = origin;
                Text = text;
                Position = position;
                LocalRotation = localRotation;
                WorldRotation = worldRotation;
                WorldAxis = worldAxis;
                Layer = layer;
                Space = space;
                Handle = handle;
                BlockPath = blockPath;
                BoundsValid = boundsValid;
                Bounds = bounds;
            }

            public string RecordKey { get; private set; }
            public string EntityType { get; private set; }
            public string Origin { get; private set; }
            public string Text { get; private set; }
            public Point3d Position { get; private set; }
            public double LocalRotation { get; private set; }
            public double WorldRotation { get; private set; }
            public Vector3d WorldAxis { get; private set; }
            public string Layer { get; private set; }
            public string Space { get; private set; }
            public string Handle { get; private set; }
            public string BlockPath { get; private set; }
            public bool BoundsValid { get; private set; }
            public BoundsData Bounds { get; private set; }
        }
    }
}
