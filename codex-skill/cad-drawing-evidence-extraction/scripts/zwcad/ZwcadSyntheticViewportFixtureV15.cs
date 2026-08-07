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
    /// Creates a synthetic DWG used only for regression testing of layout
    /// viewport visibility. It never opens or modifies a project drawing.
    ///
    /// The output path is supplied through CAD_SYNTH_VIEWPORT_OUTPUT.
    /// </summary>
    public sealed class SyntheticViewportFixtureV15
    {
        private const string LayoutName = "SYN_VIEWPORT_TEST";
        private const string LayerDamperX = "SYN-DAMPER-X";
        private const string LayerDamperY = "SYN-DAMPER-Y";
        private const string LayerContainer = "SYN-CONTAINER";
        private const string LayerViewport = "SYN-VPORT-NOPLOT";
        private const string LayerAnnotation = "SYN-ANNOTATION";
        private const string LeafBlockName = "SYN_阻尼器_设备";
        private const string ParentBlockName = "SYN_阻尼器_布置";

        private sealed class FixtureDevice
        {
            public string Id;
            public double X;
            public double Y;
            public string Layer;

            public FixtureDevice(string id, double x, double y, string layer)
            {
                Id = id;
                X = x;
                Y = y;
                Layer = layer;
            }
        }

        [CommandMethod("CADCREATESYNTHVIEWPORT15")]
        public void CreateFixture()
        {
            Document document = Application.DocumentManager.MdiActiveDocument;
            if (document == null) return;

            Editor editor = document.Editor;
            Database database = document.Database;
            string outputPath = Environment.GetEnvironmentVariable(
                "CAD_SYNTH_VIEWPORT_OUTPUT");
            if (String.IsNullOrWhiteSpace(outputPath))
            {
                editor.WriteMessage(
                    "\nCADCREATESYNTHVIEWPORT15 failed: "
                    + "CAD_SYNTH_VIEWPORT_OUTPUT is not set.");
                return;
            }
            outputPath = Path.GetFullPath(outputPath);
            Directory.CreateDirectory(Path.GetDirectoryName(outputPath));

            var devices = BuildDevices();
            var viewportHandles = new Dictionary<string, string>();
            string rootHandle = String.Empty;

            try
            {
                ObjectId layoutId = PrepareLayout(LayoutName);

                using (Transaction transaction =
                    database.TransactionManager.StartTransaction())
                {
                    ObjectId damperXLayer = EnsureLayer(
                        transaction, database, LayerDamperX, true);
                    ObjectId damperYLayer = EnsureLayer(
                        transaction, database, LayerDamperY, true);
                    EnsureLayer(transaction, database, LayerContainer, true);
                    EnsureLayer(transaction, database, LayerAnnotation, true);
                    EnsureLayer(transaction, database, LayerViewport, false);

                    ObjectId leafDefinition = CreateLeafDefinition(
                        transaction, database);
                    ObjectId parentDefinition = CreateParentDefinition(
                        transaction,
                        database,
                        leafDefinition,
                        devices);
                    rootHandle = InsertParentInModelSpace(
                        transaction, database, parentDefinition);

                    Layout layout = transaction.GetObject(
                        layoutId, OpenMode.ForRead) as Layout;
                    if (layout == null)
                        throw new InvalidOperationException("Layout unavailable.");
                    BlockTableRecord paperSpace = transaction.GetObject(
                        layout.BlockTableRecordId, OpenMode.ForWrite)
                        as BlockTableRecord;
                    if (paperSpace == null)
                        throw new InvalidOperationException(
                            "Paper space record unavailable.");

                    ClearPaperSpace(transaction, paperSpace);
                    AddPaperFrame(transaction, paperSpace);

                    Viewport leftX = AddViewport(
                        transaction,
                        paperSpace,
                        new Point3d(75.0, 145.0, 0.0),
                        120.0,
                        80.0,
                        new Point2d(2500.0, 1750.0),
                        4000.0,
                        true,
                        "LEFT_X_SHOWS_X_FREEZES_Y");
                    FreezeLayer(leftX, damperYLayer);
                    viewportHandles["LEFT_X_SHOWS_X_FREEZES_Y"] =
                        leftX.Handle.ToString();

                    Viewport leftY = AddViewport(
                        transaction,
                        paperSpace,
                        new Point3d(215.0, 145.0, 0.0),
                        120.0,
                        80.0,
                        new Point2d(2500.0, 1750.0),
                        4000.0,
                        true,
                        "LEFT_Y_SHOWS_Y_FREEZES_X");
                    FreezeLayer(leftY, damperXLayer);
                    viewportHandles["LEFT_Y_SHOWS_Y_FREEZES_X"] =
                        leftY.Handle.ToString();

                    Viewport rightAll = AddViewport(
                        transaction,
                        paperSpace,
                        new Point3d(75.0, 45.0, 0.0),
                        120.0,
                        80.0,
                        new Point2d(7500.0, 1750.0),
                        4000.0,
                        true,
                        "RIGHT_ALL_NO_FREEZE");
                    viewportHandles["RIGHT_ALL_NO_FREEZE"] =
                        rightAll.Handle.ToString();

                    Viewport offCheck = AddViewport(
                        transaction,
                        paperSpace,
                        new Point3d(215.0, 45.0, 0.0),
                        120.0,
                        80.0,
                        new Point2d(5000.0, 1750.0),
                        4000.0,
                        false,
                        "OFF_CHECK");
                    viewportHandles["OFF_CHECK"] = offCheck.Handle.ToString();

                    transaction.Commit();
                }

                database.SaveAs(outputPath, DwgVersion.Current);
                string truthPath = Path.ChangeExtension(
                    outputPath, ".ground_truth.json");
                File.WriteAllText(
                    truthPath,
                    BuildGroundTruthJson(
                        outputPath, rootHandle, devices, viewportHandles),
                    new UTF8Encoding(false));

                editor.WriteMessage(
                    "\nCADCREATESYNTHVIEWPORT15: created synthetic fixture."
                    + "\nDWG: {0}\nGround truth: {1}",
                    outputPath,
                    truthPath);
            }
            catch (System.Exception exception)
            {
                try
                {
                    File.WriteAllText(
                        outputPath + ".error.txt",
                        exception.ToString(),
                        new UTF8Encoding(false));
                }
                catch (System.Exception)
                {
                }
                editor.WriteMessage(
                    "\nCADCREATESYNTHVIEWPORT15 failed: {0}\n{1}",
                    exception.Message,
                    exception.StackTrace);
            }
        }

        private static List<FixtureDevice> BuildDevices()
        {
            return new List<FixtureDevice>
            {
                new FixtureDevice("X1", 1000.0, 1000.0, LayerDamperX),
                new FixtureDevice("X2", 2500.0, 1000.0, LayerDamperX),
                new FixtureDevice("X3_OVERLAP", 4800.0, 1000.0, LayerDamperX),
                new FixtureDevice("X4", 6500.0, 1000.0, LayerDamperX),
                new FixtureDevice("X5", 8200.0, 1000.0, LayerDamperX),
                new FixtureDevice("Y1", 1000.0, 2500.0, LayerDamperY),
                new FixtureDevice("Y2", 2500.0, 2500.0, LayerDamperY),
                new FixtureDevice("Y3_OVERLAP", 4800.0, 2500.0, LayerDamperY),
                new FixtureDevice("Y4", 6500.0, 2500.0, LayerDamperY),
                new FixtureDevice("Y5", 8200.0, 2500.0, LayerDamperY),
            };
        }

        private static ObjectId PrepareLayout(string name)
        {
            LayoutManager manager = LayoutManager.Current;
            manager.CurrentLayout = "Model";

            Document document = Application.DocumentManager.MdiActiveDocument;
            var paperLayouts = new List<string>();
            using (Transaction transaction =
                document.Database.TransactionManager.StartTransaction())
            {
                DBDictionary dictionary = transaction.GetObject(
                    document.Database.LayoutDictionaryId,
                    OpenMode.ForRead) as DBDictionary;
                foreach (DBDictionaryEntry entry in dictionary)
                {
                    Layout layout = transaction.GetObject(
                        entry.Value, OpenMode.ForRead) as Layout;
                    if (layout != null && !layout.ModelType)
                        paperLayouts.Add(layout.LayoutName);
                }
                transaction.Commit();
            }
            string retained = null;
            foreach (string layoutName in paperLayouts)
            {
                if (String.Equals(
                    layoutName, name, StringComparison.OrdinalIgnoreCase))
                {
                    retained = layoutName;
                    break;
                }
                if (retained == null) retained = layoutName;
            }
            if (retained == null)
            {
                manager.CreateLayout(name);
                retained = name;
            }
            foreach (string layoutName in paperLayouts)
            {
                if (!String.Equals(
                        layoutName,
                        retained,
                        StringComparison.OrdinalIgnoreCase)
                    && manager.LayoutExists(layoutName))
                    manager.DeleteLayout(layoutName);
            }
            if (!String.Equals(
                retained, name, StringComparison.OrdinalIgnoreCase))
            {
                manager.RenameLayout(retained, name);
            }
            manager.CurrentLayout = name;
            return manager.GetLayoutId(name);
        }

        private static ObjectId EnsureLayer(
            Transaction transaction,
            Database database,
            string name,
            bool plottable)
        {
            LayerTable table = transaction.GetObject(
                database.LayerTableId, OpenMode.ForRead) as LayerTable;
            if (table == null)
                throw new InvalidOperationException("Layer table unavailable.");
            if (table.Has(name)) return table[name];

            table.UpgradeOpen();
            var layer = new LayerTableRecord
            {
                Name = name,
                IsPlottable = plottable
            };
            ObjectId id = table.Add(layer);
            transaction.AddNewlyCreatedDBObject(layer, true);
            return id;
        }

        private static ObjectId CreateLeafDefinition(
            Transaction transaction,
            Database database)
        {
            BlockTable table = transaction.GetObject(
                database.BlockTableId, OpenMode.ForRead) as BlockTable;
            if (table == null)
                throw new InvalidOperationException("Block table unavailable.");
            if (table.Has(LeafBlockName)) return table[LeafBlockName];

            table.UpgradeOpen();
            var definition = new BlockTableRecord { Name = LeafBlockName };
            ObjectId definitionId = table.Add(definition);
            transaction.AddNewlyCreatedDBObject(definition, true);
            AddLine(
                transaction, definition,
                new Point3d(-200.0, 0.0, 0.0),
                new Point3d(200.0, 0.0, 0.0));
            AddLine(
                transaction, definition,
                new Point3d(-160.0, -80.0, 0.0),
                new Point3d(160.0, 80.0, 0.0));
            AddLine(
                transaction, definition,
                new Point3d(-160.0, 80.0, 0.0),
                new Point3d(160.0, -80.0, 0.0));
            return definitionId;
        }

        private static ObjectId CreateParentDefinition(
            Transaction transaction,
            Database database,
            ObjectId leafDefinition,
            List<FixtureDevice> devices)
        {
            BlockTable table = transaction.GetObject(
                database.BlockTableId, OpenMode.ForRead) as BlockTable;
            if (table == null)
                throw new InvalidOperationException("Block table unavailable.");
            if (table.Has(ParentBlockName)) return table[ParentBlockName];

            table.UpgradeOpen();
            var definition = new BlockTableRecord { Name = ParentBlockName };
            ObjectId definitionId = table.Add(definition);
            transaction.AddNewlyCreatedDBObject(definition, true);

            AddRectangle(
                transaction,
                definition,
                new Point3d(0.0, 0.0, 0.0),
                new Point3d(9000.0, 3500.0, 0.0),
                LayerContainer);
            AddText(
                transaction,
                definition,
                "合成阻尼器结构平面布置图（仅用于API回归，不是工程图）",
                new Point3d(200.0, 3250.0, 0.0),
                180.0,
                LayerAnnotation);

            foreach (FixtureDevice device in devices)
            {
                var reference = new BlockReference(
                    new Point3d(device.X, device.Y, 0.0), leafDefinition)
                {
                    Layer = device.Layer
                };
                definition.AppendEntity(reference);
                transaction.AddNewlyCreatedDBObject(reference, true);
                AddText(
                    transaction,
                    definition,
                    device.Id,
                    new Point3d(device.X - 160.0, device.Y + 170.0, 0.0),
                    90.0,
                    LayerAnnotation);
            }
            return definitionId;
        }

        private static string InsertParentInModelSpace(
            Transaction transaction,
            Database database,
            ObjectId parentDefinition)
        {
            BlockTable table = transaction.GetObject(
                database.BlockTableId, OpenMode.ForRead) as BlockTable;
            BlockTableRecord modelSpace = transaction.GetObject(
                table[BlockTableRecord.ModelSpace], OpenMode.ForWrite)
                as BlockTableRecord;
            var reference = new BlockReference(
                Point3d.Origin, parentDefinition)
            {
                Layer = LayerContainer
            };
            modelSpace.AppendEntity(reference);
            transaction.AddNewlyCreatedDBObject(reference, true);
            return reference.Handle.ToString();
        }

        private static void ClearPaperSpace(
            Transaction transaction,
            BlockTableRecord paperSpace)
        {
            var ids = new List<ObjectId>();
            foreach (ObjectId entityId in paperSpace) ids.Add(entityId);
            foreach (ObjectId entityId in ids)
            {
                Entity entity = transaction.GetObject(
                    entityId, OpenMode.ForWrite, false) as Entity;
                // Every paper layout owns a mandatory overall paper-space
                // viewport. ZWCAD rejects erasing it with eInvalidInput.
                Viewport viewport = entity as Viewport;
                if (viewport != null && viewport.Number == 1) continue;
                if (entity != null) entity.Erase();
            }
        }

        private static void AddPaperFrame(
            Transaction transaction,
            BlockTableRecord paperSpace)
        {
            AddRectangle(
                transaction,
                paperSpace,
                new Point3d(5.0, 2.0, 0.0),
                new Point3d(285.0, 195.0, 0.0),
                LayerAnnotation);
            AddText(
                transaction,
                paperSpace,
                "合成布局视口冻结层回归图：真值=10个唯一阻尼器",
                new Point3d(12.0, 187.0, 0.0),
                3.5,
                LayerAnnotation);
        }

        private static Viewport AddViewport(
            Transaction transaction,
            BlockTableRecord paperSpace,
            Point3d paperCenter,
            double paperWidth,
            double paperHeight,
            Point2d modelCenter,
            double modelHeight,
            bool on,
            string label)
        {
            var viewport = new Viewport
            {
                CenterPoint = paperCenter,
                Width = paperWidth,
                Height = paperHeight,
                ViewTarget = Point3d.Origin,
                ViewDirection = Vector3d.ZAxis,
                ViewCenter = modelCenter,
                ViewHeight = modelHeight,
                TwistAngle = 0.0,
                Layer = LayerViewport,
                Locked = true,
                On = on
            };
            paperSpace.AppendEntity(viewport);
            transaction.AddNewlyCreatedDBObject(viewport, true);
            viewport.On = on;
            AddText(
                transaction,
                paperSpace,
                label,
                new Point3d(
                    paperCenter.X - paperWidth / 2.0,
                    paperCenter.Y + paperHeight / 2.0 + 2.0,
                    0.0),
                2.5,
                LayerAnnotation);
            return viewport;
        }

        private static void FreezeLayer(Viewport viewport, ObjectId layerId)
        {
            var layers = new ObjectIdCollection(new[] { layerId });
            viewport.FreezeLayersInViewport(layers.GetEnumerator());
        }

        private static void AddRectangle(
            Transaction transaction,
            BlockTableRecord owner,
            Point3d minimum,
            Point3d maximum,
            string layer)
        {
            AddLayeredLine(
                transaction, owner,
                new Point3d(minimum.X, minimum.Y, 0.0),
                new Point3d(maximum.X, minimum.Y, 0.0),
                layer);
            AddLayeredLine(
                transaction, owner,
                new Point3d(maximum.X, minimum.Y, 0.0),
                new Point3d(maximum.X, maximum.Y, 0.0),
                layer);
            AddLayeredLine(
                transaction, owner,
                new Point3d(maximum.X, maximum.Y, 0.0),
                new Point3d(minimum.X, maximum.Y, 0.0),
                layer);
            AddLayeredLine(
                transaction, owner,
                new Point3d(minimum.X, maximum.Y, 0.0),
                new Point3d(minimum.X, minimum.Y, 0.0),
                layer);
        }

        private static void AddLine(
            Transaction transaction,
            BlockTableRecord owner,
            Point3d start,
            Point3d end)
        {
            var line = new Line(start, end);
            owner.AppendEntity(line);
            transaction.AddNewlyCreatedDBObject(line, true);
        }

        private static void AddLayeredLine(
            Transaction transaction,
            BlockTableRecord owner,
            Point3d start,
            Point3d end,
            string layer)
        {
            var line = new Line(start, end) { Layer = layer };
            owner.AppendEntity(line);
            transaction.AddNewlyCreatedDBObject(line, true);
        }

        private static void AddText(
            Transaction transaction,
            BlockTableRecord owner,
            string value,
            Point3d position,
            double height,
            string layer)
        {
            var text = new DBText
            {
                TextString = value,
                Position = position,
                Height = height,
                Layer = layer
            };
            owner.AppendEntity(text);
            transaction.AddNewlyCreatedDBObject(text, true);
        }

        private static string BuildGroundTruthJson(
            string drawingPath,
            string rootHandle,
            List<FixtureDevice> devices,
            Dictionary<string, string> viewportHandles)
        {
            var builder = new StringBuilder();
            builder.Append("{\n");
            builder.Append("  \"fixture\": \"synthetic_layout_viewport_v15\",\n");
            builder.Append("  \"synthetic\": true,\n");
            builder.Append("  \"not_engineering_evidence\": true,\n");
            builder.AppendFormat(
                CultureInfo.InvariantCulture,
                "  \"drawing\": \"{0}\",\n",
                Escape(drawingPath));
            builder.AppendFormat(
                CultureInfo.InvariantCulture,
                "  \"root_handle\": \"{0}\",\n",
                Escape(rootHandle));
            builder.Append("  \"expected\": {\n");
            builder.Append("    \"layout_name\": \"SYN_VIEWPORT_TEST\",\n");
            builder.Append("    \"semantic_leaf_count\": 10,\n");
            builder.Append("    \"database_visible_count\": 10,\n");
            builder.Append("    \"visible_candidate_count\": 10,\n");
            builder.Append("    \"duplicate_display_candidate_count\": 2,\n");
            builder.Append("    \"viewport_layer_frozen_occurrence_count\": 6,\n");
            builder.Append("    \"disabled_viewport_count\": 1,\n");
            builder.Append(
                "    \"overall_status\": "
                + "\"layout_viewport_visibility_consistent\"\n");
            builder.Append("  },\n");
            builder.Append("  \"devices\": [\n");
            for (int index = 0; index < devices.Count; index++)
            {
                FixtureDevice device = devices[index];
                builder.Append("    {");
                builder.AppendFormat(
                    CultureInfo.InvariantCulture,
                    "\"id\":\"{0}\",\"x\":{1},\"y\":{2},\"layer\":\"{3}\"",
                    Escape(device.Id),
                    device.X,
                    device.Y,
                    Escape(device.Layer));
                builder.Append(index + 1 == devices.Count ? "}\n" : "},\n");
            }
            builder.Append("  ],\n");
            builder.Append("  \"viewports\": {\n");
            int emitted = 0;
            foreach (KeyValuePair<string, string> item in viewportHandles)
            {
                emitted++;
                builder.AppendFormat(
                    CultureInfo.InvariantCulture,
                    "    \"{0}\": \"{1}\"{2}\n",
                    Escape(item.Key),
                    Escape(item.Value),
                    emitted == viewportHandles.Count ? "" : ",");
            }
            builder.Append("  }\n");
            builder.Append("}\n");
            return builder.ToString();
        }

        private static string Escape(string value)
        {
            if (value == null) return String.Empty;
            return value
                .Replace("\\", "\\\\")
                .Replace("\"", "\\\"")
                .Replace("\r", "\\r")
                .Replace("\n", "\\n");
        }
    }
}
