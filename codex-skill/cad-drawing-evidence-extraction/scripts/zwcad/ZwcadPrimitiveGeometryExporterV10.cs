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
    /// Read-only primitive-geometry exporter.
    ///
    /// V10 recursively expands standard blocks and exports the WCS geometry of
    /// circles, arcs, hatches, lines and polylines. V10.1 adds sampled WCS arc
    /// geometry so rotated/fan grids and concentric curved axes can be located
    /// without editing the DWG.
    /// </summary>
    public sealed class PrimitiveGeometryExporterV10
    {
        [CommandMethod("CADPRIMITIVEEXPORT10")]
        public void ExportPrimitiveGeometry()
        {
            Document document = Application.DocumentManager.MdiActiveDocument;
            if (document == null) return;

            Editor editor = document.Editor;
            Database database = document.Database;
            var records = new List<PrimitiveRecord>();
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

                                    BlockReference reference = entity as BlockReference;
                                    if (reference != null)
                                    {
                                        counters.RootBlockInstances++;
                                        ReadBlock(
                                            transaction,
                                            reference,
                                            space.Name,
                                            reference.Handle.ToString(),
                                            reference.Name,
                                            reference.Handle.ToString(),
                                            new List<Matrix3d> { reference.BlockTransform },
                                            new HashSet<ObjectId>(),
                                            records,
                                            counters);
                                        continue;
                                    }

                                    AddPrimitive(
                                        records,
                                        entity,
                                        "direct",
                                        "direct/" + entity.Handle,
                                        String.Empty,
                                        String.Empty,
                                        space.Name,
                                        new List<Matrix3d>(),
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
                        + ".cad_primitive_export_v10.json");

                File.WriteAllText(
                    outputPath,
                    ToJson(drawingPath, counters, records),
                    new UTF8Encoding(false));
                editor.WriteMessage(
                    "\nCADPRIMITIVEEXPORT10: exported {0} primitive records "
                    + "({1} direct, {2} from block definitions); {3} bounds unavailable; "
                    + "{4} invalid/special objects skipped.\n{5}",
                    records.Count,
                    counters.DirectPrimitives,
                    counters.BlockDefinitionPrimitives,
                    counters.BoundsUnavailable,
                    counters.SkippedObjectErrors,
                    outputPath);
            }
            catch (System.Exception exception)
            {
                editor.WriteMessage(
                    "\nCADPRIMITIVEEXPORT10 failed: {0}", exception.Message);
            }
        }

        private static void ReadBlock(
            Transaction transaction,
            BlockReference reference,
            string space,
            string instancePath,
            string blockPath,
            string rootInstanceHandle,
            List<Matrix3d> transforms,
            HashSet<ObjectId> definitionStack,
            List<PrimitiveRecord> records,
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

                        BlockReference nested = entity as BlockReference;
                        if (nested != null)
                        {
                            counters.NestedBlockInstances++;
                            var nestedTransforms = new List<Matrix3d>();
                            nestedTransforms.Add(nested.BlockTransform);
                            nestedTransforms.AddRange(transforms);
                            ReadBlock(
                                transaction,
                                nested,
                                space,
                                instancePath + "/" + nested.Handle,
                                blockPath + "/" + nested.Name,
                                rootInstanceHandle,
                                nestedTransforms,
                                definitionStack,
                                records,
                                counters);
                            continue;
                        }

                        AddPrimitive(
                            records,
                            entity,
                            "block-definition",
                            instancePath + "/" + entity.Handle,
                            blockPath,
                            rootInstanceHandle,
                            space,
                            transforms,
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

        private static void AddPrimitive(
            List<PrimitiveRecord> records,
            Entity entity,
            string origin,
            string recordKey,
            string blockPath,
            string rootInstanceHandle,
            string space,
            List<Matrix3d> transforms,
            ExportCounters counters)
        {
            string entityType;
            int vertexCount = 0;
            bool closed = false;
            Point3d start = Point3d.Origin;
            Point3d mid = Point3d.Origin;
            Point3d end = Point3d.Origin;
            bool endpointsValid = false;
            Point3d curveCenter = Point3d.Origin;
            bool curveGeometryValid = false;
            double curveRadius = 0.0;

            Arc arc = entity as Arc;
            Circle circle = entity as Circle;
            Hatch hatch = entity as Hatch;
            Line line = entity as Line;
            Polyline polyline = entity as Polyline;

            if (arc != null)
            {
                entityType = "Arc";
                start = Transform(arc.StartPoint, transforms);
                end = Transform(arc.EndPoint, transforms);
                double midParameter =
                    arc.StartParam + (arc.EndParam - arc.StartParam) / 2.0;
                mid = Transform(arc.GetPointAtParameter(midParameter), transforms);
                curveCenter = Transform(arc.Center, transforms);
                curveRadius = curveCenter.DistanceTo(start);
                endpointsValid = true;
                curveGeometryValid = true;
            }
            else if (circle != null)
            {
                entityType = "Circle";
            }
            else if (hatch != null)
            {
                entityType = "Hatch";
            }
            else if (line != null)
            {
                entityType = "Line";
                start = Transform(line.StartPoint, transforms);
                end = Transform(line.EndPoint, transforms);
                endpointsValid = true;
            }
            else if (polyline != null)
            {
                entityType = "Polyline";
                vertexCount = polyline.NumberOfVertices;
                closed = polyline.Closed;
            }
            else
            {
                return;
            }

            BoundsData bounds;
            bool boundsValid = TryGetWorldBounds(entity, transforms, out bounds);
            if (!boundsValid) counters.BoundsUnavailable++;

            records.Add(new PrimitiveRecord(
                recordKey,
                entityType,
                origin,
                rootInstanceHandle,
                blockPath,
                entity.Handle.ToString(),
                entity.Layer,
                entity.Linetype,
                entity.ColorIndex,
                space,
                boundsValid,
                bounds,
                endpointsValid,
                start,
                mid,
                end,
                curveGeometryValid,
                curveCenter,
                curveRadius,
                vertexCount,
                closed));

            if (origin == "direct") counters.DirectPrimitives++;
            else counters.BlockDefinitionPrimitives++;
        }

        private static Point3d Transform(Point3d point, List<Matrix3d> transforms)
        {
            Point3d transformed = point;
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
            List<PrimitiveRecord> records)
        {
            var json = new StringBuilder();
            json.Append("{\n  \"drawing\": \"").Append(Escape(drawingPath)).Append("\",");
            json.Append("\n  \"schema_revision\": \"V10.1_arc\",");
            json.Append("\n  \"scope\": \"read-only direct and recursively expanded primitive geometry in WCS\",");
            json.Append("\n  \"counting_key\": \"record_key = direct handle or block instance path plus entity handle\",");
            json.Append("\n  \"direct_primitive_count\": ").Append(counters.DirectPrimitives).Append(',');
            json.Append("\n  \"block_definition_primitive_count\": ").Append(counters.BlockDefinitionPrimitives).Append(',');
            json.Append("\n  \"root_block_instance_count\": ").Append(counters.RootBlockInstances).Append(',');
            json.Append("\n  \"nested_block_instance_count\": ").Append(counters.NestedBlockInstances).Append(',');
            json.Append("\n  \"bounds_unavailable_count\": ").Append(counters.BoundsUnavailable).Append(',');
            json.Append("\n  \"cyclic_definition_skip_count\": ").Append(counters.CyclicDefinitionsSkipped).Append(',');
            json.Append("\n  \"skipped_object_error_count\": ").Append(counters.SkippedObjectErrors).Append(',');
            json.Append("\n  \"primitive_record_count\": ").Append(records.Count).Append(',');
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
            StringBuilder json, PrimitiveRecord record)
        {
            json.Append("\n    {\"record_key\": \"").Append(Escape(record.RecordKey))
                .Append("\", \"entity_type\": \"").Append(Escape(record.EntityType))
                .Append("\", \"origin\": \"").Append(Escape(record.Origin))
                .Append("\", \"root_instance_handle\": \"").Append(Escape(record.RootInstanceHandle))
                .Append("\", \"block_path\": \"").Append(Escape(record.BlockPath))
                .Append("\", \"handle\": \"").Append(Escape(record.Handle))
                .Append("\", \"layer\": \"").Append(Escape(record.Layer))
                .Append("\", \"linetype\": \"").Append(Escape(record.Linetype))
                .Append("\", \"color_index\": ").Append(record.ColorIndex)
                .Append(", \"space\": \"").Append(Escape(record.Space))
                .Append("\", \"bounds_valid\": ").Append(record.BoundsValid ? "true" : "false");
            if (record.BoundsValid)
            {
                json.Append(", \"min_x\": ").Append(Number(record.Bounds.Min.X))
                    .Append(", \"min_y\": ").Append(Number(record.Bounds.Min.Y))
                    .Append(", \"max_x\": ").Append(Number(record.Bounds.Max.X))
                    .Append(", \"max_y\": ").Append(Number(record.Bounds.Max.Y))
                    .Append(", \"center_x\": ").Append(Number((record.Bounds.Min.X + record.Bounds.Max.X) / 2.0))
                    .Append(", \"center_y\": ").Append(Number((record.Bounds.Min.Y + record.Bounds.Max.Y) / 2.0))
                    .Append(", \"width\": ").Append(Number(record.Bounds.Max.X - record.Bounds.Min.X))
                    .Append(", \"height\": ").Append(Number(record.Bounds.Max.Y - record.Bounds.Min.Y));
            }
            else
            {
                json.Append(", \"min_x\": null, \"min_y\": null")
                    .Append(", \"max_x\": null, \"max_y\": null")
                    .Append(", \"center_x\": null, \"center_y\": null")
                    .Append(", \"width\": null, \"height\": null");
            }
            json.Append(", \"endpoints_valid\": ").Append(record.EndpointsValid ? "true" : "false");
            if (record.EndpointsValid)
            {
                json.Append(", \"start_x\": ").Append(Number(record.Start.X))
                    .Append(", \"start_y\": ").Append(Number(record.Start.Y))
                    .Append(", \"end_x\": ").Append(Number(record.End.X))
                    .Append(", \"end_y\": ").Append(Number(record.End.Y));
            }
            else
            {
                json.Append(", \"start_x\": null, \"start_y\": null")
                    .Append(", \"end_x\": null, \"end_y\": null");
            }
            json.Append(", \"curve_geometry_valid\": ")
                .Append(record.CurveGeometryValid ? "true" : "false");
            if (record.CurveGeometryValid)
            {
                json.Append(", \"curve_center_x\": ").Append(Number(record.CurveCenter.X))
                    .Append(", \"curve_center_y\": ").Append(Number(record.CurveCenter.Y))
                    .Append(", \"curve_mid_x\": ").Append(Number(record.Mid.X))
                    .Append(", \"curve_mid_y\": ").Append(Number(record.Mid.Y))
                    .Append(", \"curve_radius\": ").Append(Number(record.CurveRadius));
            }
            else
            {
                json.Append(", \"curve_center_x\": null, \"curve_center_y\": null")
                    .Append(", \"curve_mid_x\": null, \"curve_mid_y\": null")
                    .Append(", \"curve_radius\": null");
            }
            json.Append(", \"vertex_count\": ").Append(record.VertexCount)
                .Append(", \"closed\": ").Append(record.Closed ? "true" : "false")
                .Append('}');
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
            public int DirectPrimitives;
            public int BlockDefinitionPrimitives;
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

        private sealed class PrimitiveRecord
        {
            public PrimitiveRecord(
                string recordKey,
                string entityType,
                string origin,
                string rootInstanceHandle,
                string blockPath,
                string handle,
                string layer,
                string linetype,
                int colorIndex,
                string space,
                bool boundsValid,
                BoundsData bounds,
                bool endpointsValid,
                Point3d start,
                Point3d mid,
                Point3d end,
                bool curveGeometryValid,
                Point3d curveCenter,
                double curveRadius,
                int vertexCount,
                bool closed)
            {
                RecordKey = recordKey;
                EntityType = entityType;
                Origin = origin;
                RootInstanceHandle = rootInstanceHandle;
                BlockPath = blockPath;
                Handle = handle;
                Layer = layer;
                Linetype = linetype;
                ColorIndex = colorIndex;
                Space = space;
                BoundsValid = boundsValid;
                Bounds = bounds;
                EndpointsValid = endpointsValid;
                Start = start;
                Mid = mid;
                End = end;
                CurveGeometryValid = curveGeometryValid;
                CurveCenter = curveCenter;
                CurveRadius = curveRadius;
                VertexCount = vertexCount;
                Closed = closed;
            }

            public string RecordKey { get; private set; }
            public string EntityType { get; private set; }
            public string Origin { get; private set; }
            public string RootInstanceHandle { get; private set; }
            public string BlockPath { get; private set; }
            public string Handle { get; private set; }
            public string Layer { get; private set; }
            public string Linetype { get; private set; }
            public int ColorIndex { get; private set; }
            public string Space { get; private set; }
            public bool BoundsValid { get; private set; }
            public BoundsData Bounds { get; private set; }
            public bool EndpointsValid { get; private set; }
            public Point3d Start { get; private set; }
            public Point3d Mid { get; private set; }
            public Point3d End { get; private set; }
            public bool CurveGeometryValid { get; private set; }
            public Point3d CurveCenter { get; private set; }
            public double CurveRadius { get; private set; }
            public int VertexCount { get; private set; }
            public bool Closed { get; private set; }
        }
    }
}
