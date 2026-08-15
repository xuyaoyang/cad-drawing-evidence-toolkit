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
    /// Read-only visibility evidence exporter.
    ///
    /// It records database-level visibility for every root and recursively expanded
    /// nested BlockReference. Layer 0 inside a block inherits the effective layer
    /// of the containing block reference. Viewport layer freezes are exported as
    /// separate evidence because a model-space entity can be visible in one paper
    /// viewport and hidden in another.
    /// </summary>
    public sealed class VisibilityExporterV13
    {
        [CommandMethod("CADVISIBILITYEXPORT13")]
        public void ExportVisibility()
        {
            Document document = Application.DocumentManager.MdiActiveDocument;
            if (document == null) return;

            Editor editor = document.Editor;
            Database database = document.Database;
            var instances = new List<InstanceVisibilityRecord>();
            var layers = new List<LayerVisibilityRecord>();
            var layouts = new List<LayoutVisibilityRecord>();
            var viewports = new List<ViewportVisibilityRecord>();
            var counters = new ExportCounters();

            try
            {
                using (Transaction transaction = database.TransactionManager.StartTransaction())
                {
                    ReadLayers(transaction, database, layers, counters);
                    Dictionary<ObjectId, LayoutVisibilityRecord> layoutsBySpace =
                        ReadLayouts(transaction, database, layouts, counters);
                    BlockTable table = transaction.GetObject(
                        database.BlockTableId, OpenMode.ForRead) as BlockTable;
                    if (table == null) throw new InvalidOperationException("BlockTable unavailable.");

                    foreach (ObjectId blockId in table)
                    {
                        BlockTableRecord space = SafeGet<BlockTableRecord>(
                            transaction, blockId, counters);
                        if (space == null || !space.IsLayout || space.IsFromExternalReference)
                            continue;
                        LayoutVisibilityRecord layout = null;
                        layoutsBySpace.TryGetValue(space.ObjectId, out layout);

                        foreach (ObjectId entityId in space)
                        {
                            Entity entity = SafeGet<Entity>(transaction, entityId, counters);
                            if (entity == null) continue;

                            Viewport viewport = entity as Viewport;
                            if (viewport != null)
                            {
                                ReadViewport(
                                    transaction,
                                    space.Name,
                                    space.Handle.ToString(),
                                    layout,
                                    viewport,
                                    viewports,
                                    counters);
                            }

                            BlockReference reference = entity as BlockReference;
                            if (reference == null) continue;
                            counters.RootInstances++;
                            string rootHandle = reference.Handle.ToString();
                            ReadInstance(
                                transaction,
                                reference,
                                space.Name,
                                String.Empty,
                                String.Empty,
                                rootHandle,
                                String.Empty,
                                true,
                                null,
                                new HashSet<ObjectId>(),
                                instances,
                                counters);
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
                        + ".cad_visibility_export_v13.json");

                File.WriteAllText(
                    outputPath,
                    ToJson(
                        drawingPath,
                        counters,
                        instances,
                        layers,
                        layouts,
                        viewports),
                    new UTF8Encoding(false));
                editor.WriteMessage(
                    "\nCADVISIBILITYEXPORT13: exported {0} instances, {1} layers, "
                        + "{2} viewports; {3} hidden by database visibility, "
                        + "{4} unknown, {5} object errors.\n{6}",
                    instances.Count,
                    layers.Count,
                    viewports.Count,
                    counters.HiddenInstances,
                    counters.UnknownVisibilityInstances,
                    counters.SkippedObjectErrors,
                    outputPath);
            }
            catch (System.Exception exception)
            {
                editor.WriteMessage(
                    "\nCADVISIBILITYEXPORT13 failed: {0}\n{1}",
                    exception.Message,
                    exception.StackTrace);
            }
        }

        private static void ReadLayers(
            Transaction transaction,
            Database database,
            List<LayerVisibilityRecord> records,
            ExportCounters counters)
        {
            LayerTable table = SafeGet<LayerTable>(
                transaction, database.LayerTableId, counters);
            if (table == null) return;
            foreach (ObjectId layerId in table)
            {
                LayerState state = ReadLayerState(transaction, layerId, counters);
                if (state == null) continue;
                records.Add(new LayerVisibilityRecord(state));
            }
        }

        private static Dictionary<ObjectId, LayoutVisibilityRecord> ReadLayouts(
            Transaction transaction,
            Database database,
            List<LayoutVisibilityRecord> records,
            ExportCounters counters)
        {
            var bySpace = new Dictionary<ObjectId, LayoutVisibilityRecord>();
            DBDictionary dictionary = SafeGet<DBDictionary>(
                transaction, database.LayoutDictionaryId, counters);
            if (dictionary == null) return bySpace;

            foreach (DBDictionaryEntry entry in dictionary)
            {
                Layout layout = SafeGet<Layout>(
                    transaction, entry.Value, counters);
                if (layout == null) continue;
                BlockTableRecord space = SafeGet<BlockTableRecord>(
                    transaction, layout.BlockTableRecordId, counters);
                var record = new LayoutVisibilityRecord(
                    layout.LayoutName,
                    layout.Handle.ToString(),
                    space == null ? String.Empty : space.Handle.ToString(),
                    space == null ? String.Empty : space.Name,
                    layout.TabOrder,
                    layout.ModelType);
                records.Add(record);
                bySpace[layout.BlockTableRecordId] = record;
            }
            return bySpace;
        }

        private static void ReadViewport(
            Transaction transaction,
            string space,
            string spaceRecordHandle,
            LayoutVisibilityRecord layout,
            Viewport viewport,
            List<ViewportVisibilityRecord> records,
            ExportCounters counters)
        {
            var frozenLayers = new List<string>();
            try
            {
                foreach (ObjectId layerId in viewport.GetFrozenLayers())
                {
                    LayerTableRecord layer = SafeGet<LayerTableRecord>(
                        transaction, layerId, counters);
                    if (layer != null) frozenLayers.Add(layer.Name);
                }
            }
            catch (System.Exception)
            {
                counters.ViewportFrozenLayerReadErrors++;
            }

            LayerState layerState = ReadLayerState(
                transaction, viewport.LayerId, counters);
            bool entityVisible = SafeVisible(viewport, counters);
            double centerX = 0.0;
            double centerY = 0.0;
            double centerZ = 0.0;
            double paperWidth = 0.0;
            double paperHeight = 0.0;
            double viewCenterX = 0.0;
            double viewCenterY = 0.0;
            double viewTargetX = 0.0;
            double viewTargetY = 0.0;
            double viewTargetZ = 0.0;
            double viewHeight = 0.0;
            double customScale = 0.0;
            double twistAngle = 0.0;
            double viewDirectionX = 0.0;
            double viewDirectionY = 0.0;
            double viewDirectionZ = 0.0;
            bool nonRectClipOn = false;
            string nonRectClipHandle = String.Empty;
            try
            {
                Point3d centerPoint = viewport.CenterPoint;
                Point2d viewCenter = viewport.ViewCenter;
                Point3d viewTarget = viewport.ViewTarget;
                Vector3d viewDirection = viewport.ViewDirection;
                centerX = centerPoint.X;
                centerY = centerPoint.Y;
                centerZ = centerPoint.Z;
                paperWidth = viewport.Width;
                paperHeight = viewport.Height;
                viewCenterX = viewCenter.X;
                viewCenterY = viewCenter.Y;
                viewTargetX = viewTarget.X;
                viewTargetY = viewTarget.Y;
                viewTargetZ = viewTarget.Z;
                viewHeight = viewport.ViewHeight;
                customScale = viewport.CustomScale;
                twistAngle = viewport.TwistAngle;
                viewDirectionX = viewDirection.X;
                viewDirectionY = viewDirection.Y;
                viewDirectionZ = viewDirection.Z;
                nonRectClipOn = viewport.NonRectClipOn;
                ObjectId clipId = viewport.NonRectClipEntityId;
                if (!clipId.IsNull) nonRectClipHandle = clipId.Handle.ToString();
            }
            catch (System.Exception)
            {
                counters.ViewportReadErrors++;
            }
            records.Add(new ViewportVisibilityRecord(
                space,
                spaceRecordHandle,
                layout == null ? String.Empty : layout.LayoutName,
                layout == null ? String.Empty : layout.LayoutHandle,
                layout == null ? -1 : layout.TabOrder,
                viewport.Handle.ToString(),
                viewport.Number,
                viewport.Number == 1,
                SafeViewportOn(viewport, counters),
                entityVisible,
                viewport.Locked,
                centerX,
                centerY,
                centerZ,
                paperWidth,
                paperHeight,
                viewCenterX,
                viewCenterY,
                viewTargetX,
                viewTargetY,
                viewTargetZ,
                viewHeight,
                customScale,
                twistAngle,
                viewDirectionX,
                viewDirectionY,
                viewDirectionZ,
                nonRectClipOn,
                nonRectClipHandle,
                layerState,
                frozenLayers));
        }

        private static void ReadInstance(
            Transaction transaction,
            BlockReference reference,
            string space,
            string parentInstanceKey,
            string parentEntityHandle,
            string rootHandle,
            string parentNamePath,
            bool parentEffectiveVisible,
            LayerState parentEffectiveLayer,
            HashSet<ObjectId> definitionStack,
            List<InstanceVisibilityRecord> records,
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

            LayerState ownLayer = ReadLayerState(
                transaction, reference.LayerId, counters);
            bool inheritsLayerZero = !String.IsNullOrEmpty(parentInstanceKey)
                && String.Equals(reference.Layer, "0", StringComparison.OrdinalIgnoreCase)
                && parentEffectiveLayer != null;
            LayerState effectiveLayer = inheritsLayerZero
                ? parentEffectiveLayer
                : ownLayer;

            bool entityVisible = SafeVisible(reference, counters);
            bool effectiveVisible = parentEffectiveVisible
                && entityVisible
                && effectiveLayer != null
                && !effectiveLayer.IsOff
                && !effectiveLayer.IsFrozen
                && !effectiveLayer.IsHidden;
            string visibilityReason = BuildVisibilityReason(
                parentEffectiveVisible,
                entityVisible,
                effectiveLayer,
                inheritsLayerZero);

            bool isDynamic = false;
            string effectiveName = blockName;
            string definitionHandle = String.Empty;
            BlockTableRecord definition = null;
            try
            {
                definition = transaction.GetObject(
                    reference.BlockTableRecord, OpenMode.ForRead) as BlockTableRecord;
                if (definition != null) definitionHandle = definition.Handle.ToString();
            }
            catch (System.Exception exception)
            {
                counters.AddObjectError(
                    "BlockTableRecord",
                    SafeObjectHandle(reference.BlockTableRecord),
                    exception.Message);
            }
            TryGetDynamicName(
                transaction, reference, ref isDynamic, ref effectiveName, counters);
            List<DynamicPropertyRecord> dynamicProperties = ReadDynamicProperties(
                reference, isDynamic, counters);

            if (!effectiveVisible) counters.HiddenInstances++;
            if (effectiveLayer == null) counters.UnknownVisibilityInstances++;

            records.Add(new InstanceVisibilityRecord(
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
                ownLayer,
                effectiveLayer,
                inheritsLayerZero,
                entityVisible,
                parentEffectiveVisible,
                effectiveVisible,
                visibilityReason,
                dynamicProperties));

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
                foreach (ObjectId entityId in definition)
                {
                    BlockReference nested = SafeGet<BlockReference>(
                        transaction, entityId, counters);
                    if (nested == null) continue;
                    counters.NestedInstances++;
                    ReadInstance(
                        transaction,
                        nested,
                        space,
                        instanceKey,
                        entityHandle,
                        rootHandle,
                        namePath,
                        effectiveVisible,
                        effectiveLayer,
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

        private static T SafeGet<T>(
            Transaction transaction,
            ObjectId objectId,
            ExportCounters counters) where T : DBObject
        {
            try
            {
                return transaction.GetObject(objectId, OpenMode.ForRead) as T;
            }
            catch (System.Exception exception)
            {
                counters.AddObjectError(
                    typeof(T).Name,
                    SafeObjectHandle(objectId),
                    exception.Message);
                return null;
            }
        }

        private static string SafeObjectHandle(ObjectId objectId)
        {
            try
            {
                return objectId.IsNull
                    ? String.Empty
                    : objectId.Handle.ToString();
            }
            catch (System.Exception)
            {
                return String.Empty;
            }
        }

        private static LayerState ReadLayerState(
            Transaction transaction,
            ObjectId layerId,
            ExportCounters counters)
        {
            LayerTableRecord layer = SafeGet<LayerTableRecord>(
                transaction, layerId, counters);
            if (layer == null) return null;
            try
            {
                return new LayerState(
                    layer.Handle.ToString(),
                    layer.Name,
                    layer.IsOff,
                    layer.IsFrozen,
                    layer.IsHidden,
                    layer.IsLocked,
                    layer.IsPlottable,
                    layer.ViewportVisibilityDefault);
            }
            catch (System.Exception)
            {
                counters.LayerReadErrors++;
                return null;
            }
        }

        private static bool SafeVisible(Entity entity, ExportCounters counters)
        {
            try { return entity.Visible; }
            catch (System.Exception)
            {
                counters.EntityVisibilityReadErrors++;
                return false;
            }
        }

        private static bool SafeViewportOn(
            Viewport viewport, ExportCounters counters)
        {
            try { return viewport.On; }
            catch (System.Exception)
            {
                counters.ViewportReadErrors++;
                return false;
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
            ref string effectiveName,
            ExportCounters counters)
        {
            try
            {
                isDynamic = reference.IsDynamicBlock;
                if (!isDynamic) return;
                BlockTableRecord dynamicDefinition = transaction.GetObject(
                    reference.DynamicBlockTableRecord,
                    OpenMode.ForRead) as BlockTableRecord;
                if (dynamicDefinition != null
                    && !String.IsNullOrWhiteSpace(dynamicDefinition.Name))
                    effectiveName = dynamicDefinition.Name;
            }
            catch (System.Exception)
            {
                counters.DynamicPropertyReadErrors++;
                isDynamic = false;
            }
        }

        private static List<DynamicPropertyRecord> ReadDynamicProperties(
            BlockReference reference,
            bool isDynamic,
            ExportCounters counters)
        {
            var result = new List<DynamicPropertyRecord>();
            if (!isDynamic) return result;
            try
            {
                foreach (DynamicBlockReferenceProperty property
                    in reference.DynamicBlockReferencePropertyCollection)
                {
                    result.Add(new DynamicPropertyRecord(
                        property.PropertyName,
                        property.Description,
                        ValueToString(property.Value),
                        property.ReadOnly,
                        property.Show,
                        property.VisibleInCurrentVisibilityState,
                        property.UnitsType.ToString(),
                        property.PropertyTypeCode));
                }
            }
            catch (System.Exception)
            {
                counters.DynamicPropertyReadErrors++;
            }
            return result;
        }

        private static string BuildVisibilityReason(
            bool parentVisible,
            bool entityVisible,
            LayerState effectiveLayer,
            bool inheritedLayerZero)
        {
            var reasons = new List<string>();
            if (!parentVisible) reasons.Add("hidden_parent");
            if (!entityVisible) reasons.Add("entity_visible_false");
            if (effectiveLayer == null)
            {
                reasons.Add("effective_layer_unreadable");
            }
            else
            {
                if (effectiveLayer.IsOff) reasons.Add("effective_layer_off");
                if (effectiveLayer.IsFrozen) reasons.Add("effective_layer_frozen");
                if (effectiveLayer.IsHidden) reasons.Add("effective_layer_hidden");
            }
            if (inheritedLayerZero) reasons.Add("layer0_inherits_parent_layer");
            if (reasons.Count == 0) reasons.Add("visible_in_database");
            return String.Join(";", reasons.ToArray());
        }

        private static string ValueToString(object value)
        {
            if (value == null) return String.Empty;
            IFormattable formattable = value as IFormattable;
            if (formattable != null)
                return formattable.ToString(null, CultureInfo.InvariantCulture);
            return value.ToString();
        }

        private static string ToJson(
            string drawingPath,
            ExportCounters counters,
            List<InstanceVisibilityRecord> instances,
            List<LayerVisibilityRecord> layers,
            List<LayoutVisibilityRecord> layouts,
            List<ViewportVisibilityRecord> viewports)
        {
            var json = new StringBuilder();
            json.Append("{\n  \"drawing\": \"").Append(Escape(drawingPath)).Append("\",");
            json.Append("\n  \"scope\": \"read-only database visibility, dynamic properties, layers and paper-space viewport layer freezes\",");
            json.Append("\n  \"visibility_boundary\": \"database visibility is not proof of visibility in every plotted viewport\",");
            json.Append("\n  \"root_instance_count\": ").Append(counters.RootInstances).Append(',');
            json.Append("\n  \"nested_instance_count\": ").Append(counters.NestedInstances).Append(',');
            json.Append("\n  \"hidden_database_instance_count\": ").Append(counters.HiddenInstances).Append(',');
            json.Append("\n  \"unknown_visibility_instance_count\": ").Append(counters.UnknownVisibilityInstances).Append(',');
            json.Append("\n  \"skipped_object_error_count\": ").Append(counters.SkippedObjectErrors).Append(',');
            json.Append("\n  \"cyclic_definition_skip_count\": ").Append(counters.CyclicDefinitionsSkipped).Append(',');
            json.Append("\n  \"dynamic_property_read_error_count\": ").Append(counters.DynamicPropertyReadErrors).Append(',');
            json.Append("\n  \"layer_read_error_count\": ").Append(counters.LayerReadErrors).Append(',');
            json.Append("\n  \"entity_visibility_read_error_count\": ").Append(counters.EntityVisibilityReadErrors).Append(',');
            json.Append("\n  \"viewport_read_error_count\": ").Append(counters.ViewportReadErrors).Append(',');
            json.Append("\n  \"viewport_frozen_layer_read_error_count\": ").Append(counters.ViewportFrozenLayerReadErrors).Append(',');
            json.Append("\n  \"instance_record_count\": ").Append(instances.Count).Append(',');
            json.Append("\n  \"layer_record_count\": ").Append(layers.Count).Append(',');
            json.Append("\n  \"layout_record_count\": ").Append(layouts.Count).Append(',');
            json.Append("\n  \"viewport_record_count\": ").Append(viewports.Count).Append(',');
            json.Append("\n  \"object_errors\": [");
            for (int i = 0; i < counters.ObjectErrors.Count; i++)
            {
                if (i > 0) json.Append(',');
                AppendObjectError(json, counters.ObjectErrors[i]);
            }
            json.Append("\n  ],\n  \"records\": [");
            for (int i = 0; i < instances.Count; i++)
            {
                if (i > 0) json.Append(',');
                AppendInstance(json, instances[i]);
            }
            json.Append("\n  ],\n  \"layers\": [");
            for (int i = 0; i < layers.Count; i++)
            {
                if (i > 0) json.Append(',');
                AppendLayer(json, layers[i].State);
            }
            json.Append("\n  ],\n  \"layouts\": [");
            for (int i = 0; i < layouts.Count; i++)
            {
                if (i > 0) json.Append(',');
                AppendLayout(json, layouts[i]);
            }
            json.Append("\n  ],\n  \"viewports\": [");
            for (int i = 0; i < viewports.Count; i++)
            {
                if (i > 0) json.Append(',');
                AppendViewport(json, viewports[i]);
            }
            json.Append("\n  ]\n}\n");
            return json.ToString();
        }

        private static void AppendInstance(
            StringBuilder json, InstanceVisibilityRecord record)
        {
            json.Append("\n    {\"instance_key\": \"").Append(Escape(record.InstanceKey))
                .Append("\", \"instance_handle\": \"").Append(Escape(record.InstanceHandle))
                .Append("\", \"definition_handle\": \"").Append(Escape(record.DefinitionHandle))
                .Append("\", \"parent_instance_key\": \"").Append(Escape(record.ParentInstanceKey))
                .Append("\", \"parent_instance_handle\": \"").Append(Escape(record.ParentInstanceHandle))
                .Append("\", \"root_instance_handle\": \"").Append(Escape(record.RootInstanceHandle))
                .Append("\", \"name_path\": \"").Append(Escape(record.NamePath))
                .Append("\", \"block_name\": \"").Append(Escape(record.BlockName))
                .Append("\", \"effective_name\": \"").Append(Escape(record.EffectiveName))
                .Append("\", \"is_dynamic\": ").Append(Bool(record.IsDynamic))
                .Append(", \"space\": \"").Append(Escape(record.Space))
                .Append("\", \"own_layer\": \"").Append(Escape(record.OwnLayerName))
                .Append("\", \"effective_layer\": \"")
                .Append(Escape(record.EffectiveLayer == null
                    ? String.Empty : record.EffectiveLayer.Name))
                .Append("\", \"inherits_layer_zero\": ").Append(Bool(record.InheritsLayerZero))
                .Append(", \"entity_visible\": ").Append(Bool(record.EntityVisible))
                .Append(", \"parent_effective_visible\": ").Append(Bool(record.ParentEffectiveVisible))
                .Append(", \"effective_visible_database\": ").Append(Bool(record.EffectiveVisible))
                .Append(", \"visibility_reason\": \"").Append(Escape(record.VisibilityReason))
                .Append("\", \"own_layer_state\": ");
            AppendNullableLayer(json, record.OwnLayer);
            json.Append(", \"effective_layer_state\": ");
            AppendNullableLayer(json, record.EffectiveLayer);
            json.Append(", \"dynamic_properties\": [");
            for (int i = 0; i < record.DynamicProperties.Count; i++)
            {
                if (i > 0) json.Append(',');
                DynamicPropertyRecord property = record.DynamicProperties[i];
                json.Append("{\"name\":\"").Append(Escape(property.Name))
                    .Append("\",\"description\":\"").Append(Escape(property.Description))
                    .Append("\",\"value\":\"").Append(Escape(property.Value))
                    .Append("\",\"read_only\":").Append(Bool(property.ReadOnly))
                    .Append(",\"show\":").Append(Bool(property.Show))
                    .Append(",\"visible_in_current_visibility_state\":")
                    .Append(Bool(property.VisibleInCurrentVisibilityState))
                    .Append(",\"units_type\":\"").Append(Escape(property.UnitsType))
                    .Append("\",\"property_type_code\":").Append(property.PropertyTypeCode)
                    .Append('}');
            }
            json.Append("]}");
        }

        private static void AppendLayer(StringBuilder json, LayerState state)
        {
            json.Append("\n    {");
            AppendLayerFields(json, state);
            json.Append('}');
        }

        private static void AppendNullableLayer(
            StringBuilder json, LayerState state)
        {
            if (state == null)
            {
                json.Append("null");
                return;
            }
            json.Append('{');
            AppendLayerFields(json, state);
            json.Append('}');
        }

        private static void AppendLayerFields(
            StringBuilder json, LayerState state)
        {
            json.Append("\"handle\":\"").Append(Escape(state.Handle))
                .Append("\",\"name\":\"").Append(Escape(state.Name))
                .Append("\",\"is_off\":").Append(Bool(state.IsOff))
                .Append(",\"is_frozen\":").Append(Bool(state.IsFrozen))
                .Append(",\"is_hidden\":").Append(Bool(state.IsHidden))
                .Append(",\"is_locked\":").Append(Bool(state.IsLocked))
                .Append(",\"is_plottable\":").Append(Bool(state.IsPlottable))
                .Append(",\"viewport_visibility_default\":")
                .Append(Bool(state.ViewportVisibilityDefault));
        }

        private static void AppendViewport(
            StringBuilder json, ViewportVisibilityRecord record)
        {
            json.Append("\n    {\"space\":\"").Append(Escape(record.Space))
                .Append("\",\"space_record_handle\":\"")
                .Append(Escape(record.SpaceRecordHandle))
                .Append("\",\"layout_name\":\"").Append(Escape(record.LayoutName))
                .Append("\",\"layout_handle\":\"").Append(Escape(record.LayoutHandle))
                .Append("\",\"layout_tab_order\":").Append(record.LayoutTabOrder)
                .Append(",\"handle\":\"").Append(Escape(record.Handle))
                .Append("\",\"number\":").Append(record.Number)
                .Append(",\"is_paper_viewport\":")
                .Append(Bool(record.IsPaperViewport))
                .Append(",\"on\":").Append(Bool(record.On))
                .Append(",\"entity_visible\":").Append(Bool(record.EntityVisible))
                .Append(",\"locked\":").Append(Bool(record.Locked))
                .Append(",\"paper_center_x\":").Append(Number(record.CenterX))
                .Append(",\"paper_center_y\":").Append(Number(record.CenterY))
                .Append(",\"paper_center_z\":").Append(Number(record.CenterZ))
                .Append(",\"paper_width\":").Append(Number(record.PaperWidth))
                .Append(",\"paper_height\":").Append(Number(record.PaperHeight))
                .Append(",\"view_center_x\":").Append(Number(record.ViewCenterX))
                .Append(",\"view_center_y\":").Append(Number(record.ViewCenterY))
                .Append(",\"view_target_x\":").Append(Number(record.ViewTargetX))
                .Append(",\"view_target_y\":").Append(Number(record.ViewTargetY))
                .Append(",\"view_target_z\":").Append(Number(record.ViewTargetZ))
                .Append(",\"view_height\":").Append(Number(record.ViewHeight))
                .Append(",\"custom_scale\":").Append(Number(record.CustomScale))
                .Append(",\"twist_angle\":").Append(Number(record.TwistAngle))
                .Append(",\"view_direction_x\":").Append(Number(record.ViewDirectionX))
                .Append(",\"view_direction_y\":").Append(Number(record.ViewDirectionY))
                .Append(",\"view_direction_z\":").Append(Number(record.ViewDirectionZ))
                .Append(",\"non_rect_clip_on\":").Append(Bool(record.NonRectClipOn))
                .Append(",\"non_rect_clip_handle\":\"")
                .Append(Escape(record.NonRectClipHandle)).Append('"')
                .Append(",\"layer_state\":");
            AppendNullableLayer(json, record.Layer);
            json.Append(",\"frozen_layers\":[");
            for (int i = 0; i < record.FrozenLayers.Count; i++)
            {
                if (i > 0) json.Append(',');
                json.Append('"').Append(Escape(record.FrozenLayers[i])).Append('"');
            }
            json.Append("]}");
        }

        private static void AppendLayout(
            StringBuilder json, LayoutVisibilityRecord record)
        {
            json.Append("\n    {\"layout_name\":\"")
                .Append(Escape(record.LayoutName))
                .Append("\",\"layout_handle\":\"").Append(Escape(record.LayoutHandle))
                .Append("\",\"space_record_handle\":\"")
                .Append(Escape(record.SpaceRecordHandle))
                .Append("\",\"space_record_name\":\"")
                .Append(Escape(record.SpaceRecordName))
                .Append("\",\"tab_order\":").Append(record.TabOrder)
                .Append(",\"model_type\":").Append(Bool(record.ModelType))
                .Append('}');
        }

        private static void AppendObjectError(
            StringBuilder json, ObjectErrorRecord record)
        {
            json.Append("\n    {\"requested_type\":\"")
                .Append(Escape(record.RequestedType))
                .Append("\",\"object_handle\":\"")
                .Append(Escape(record.ObjectHandle))
                .Append("\",\"message\":\"")
                .Append(Escape(record.Message))
                .Append("\"}");
        }

        private static string Bool(bool value)
        {
            return value ? "true" : "false";
        }

        private static string Number(double value)
        {
            if (Double.IsNaN(value) || Double.IsInfinity(value)) return "0";
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
            public int HiddenInstances;
            public int UnknownVisibilityInstances;
            public int SkippedObjectErrors;
            public int CyclicDefinitionsSkipped;
            public int DynamicPropertyReadErrors;
            public int LayerReadErrors;
            public int EntityVisibilityReadErrors;
            public int ViewportReadErrors;
            public int ViewportFrozenLayerReadErrors;
            public readonly List<ObjectErrorRecord> ObjectErrors =
                new List<ObjectErrorRecord>();

            public void AddObjectError(
                string requestedType, string objectHandle, string message)
            {
                SkippedObjectErrors++;
                ObjectErrors.Add(
                    new ObjectErrorRecord(
                        requestedType,
                        objectHandle,
                        message));
            }
        }

        private sealed class ObjectErrorRecord
        {
            public ObjectErrorRecord(
                string requestedType,
                string objectHandle,
                string message)
            {
                RequestedType = requestedType;
                ObjectHandle = objectHandle;
                Message = message;
            }

            public string RequestedType { get; private set; }
            public string ObjectHandle { get; private set; }
            public string Message { get; private set; }
        }

        private sealed class LayerState
        {
            public LayerState(
                string handle,
                string name,
                bool isOff,
                bool isFrozen,
                bool isHidden,
                bool isLocked,
                bool isPlottable,
                bool viewportVisibilityDefault)
            {
                Handle = handle;
                Name = name;
                IsOff = isOff;
                IsFrozen = isFrozen;
                IsHidden = isHidden;
                IsLocked = isLocked;
                IsPlottable = isPlottable;
                ViewportVisibilityDefault = viewportVisibilityDefault;
            }

            public string Handle { get; private set; }
            public string Name { get; private set; }
            public bool IsOff { get; private set; }
            public bool IsFrozen { get; private set; }
            public bool IsHidden { get; private set; }
            public bool IsLocked { get; private set; }
            public bool IsPlottable { get; private set; }
            public bool ViewportVisibilityDefault { get; private set; }
        }

        private sealed class LayerVisibilityRecord
        {
            public LayerVisibilityRecord(LayerState state) { State = state; }
            public LayerState State { get; private set; }
        }

        private sealed class DynamicPropertyRecord
        {
            public DynamicPropertyRecord(
                string name,
                string description,
                string value,
                bool readOnly,
                bool show,
                bool visibleInCurrentVisibilityState,
                string unitsType,
                short propertyTypeCode)
            {
                Name = name ?? String.Empty;
                Description = description ?? String.Empty;
                Value = value ?? String.Empty;
                ReadOnly = readOnly;
                Show = show;
                VisibleInCurrentVisibilityState = visibleInCurrentVisibilityState;
                UnitsType = unitsType ?? String.Empty;
                PropertyTypeCode = propertyTypeCode;
            }

            public string Name { get; private set; }
            public string Description { get; private set; }
            public string Value { get; private set; }
            public bool ReadOnly { get; private set; }
            public bool Show { get; private set; }
            public bool VisibleInCurrentVisibilityState { get; private set; }
            public string UnitsType { get; private set; }
            public short PropertyTypeCode { get; private set; }
        }

        private sealed class InstanceVisibilityRecord
        {
            public InstanceVisibilityRecord(
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
                string ownLayerName,
                LayerState ownLayer,
                LayerState effectiveLayer,
                bool inheritsLayerZero,
                bool entityVisible,
                bool parentEffectiveVisible,
                bool effectiveVisible,
                string visibilityReason,
                List<DynamicPropertyRecord> dynamicProperties)
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
                OwnLayerName = ownLayerName;
                OwnLayer = ownLayer;
                EffectiveLayer = effectiveLayer;
                InheritsLayerZero = inheritsLayerZero;
                EntityVisible = entityVisible;
                ParentEffectiveVisible = parentEffectiveVisible;
                EffectiveVisible = effectiveVisible;
                VisibilityReason = visibilityReason;
                DynamicProperties = dynamicProperties;
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
            public string OwnLayerName { get; private set; }
            public LayerState OwnLayer { get; private set; }
            public LayerState EffectiveLayer { get; private set; }
            public bool InheritsLayerZero { get; private set; }
            public bool EntityVisible { get; private set; }
            public bool ParentEffectiveVisible { get; private set; }
            public bool EffectiveVisible { get; private set; }
            public string VisibilityReason { get; private set; }
            public List<DynamicPropertyRecord> DynamicProperties { get; private set; }
        }

        private sealed class LayoutVisibilityRecord
        {
            public LayoutVisibilityRecord(
                string layoutName,
                string layoutHandle,
                string spaceRecordHandle,
                string spaceRecordName,
                int tabOrder,
                bool modelType)
            {
                LayoutName = layoutName;
                LayoutHandle = layoutHandle;
                SpaceRecordHandle = spaceRecordHandle;
                SpaceRecordName = spaceRecordName;
                TabOrder = tabOrder;
                ModelType = modelType;
            }

            public string LayoutName { get; private set; }
            public string LayoutHandle { get; private set; }
            public string SpaceRecordHandle { get; private set; }
            public string SpaceRecordName { get; private set; }
            public int TabOrder { get; private set; }
            public bool ModelType { get; private set; }
        }

        private sealed class ViewportVisibilityRecord
        {
            public ViewportVisibilityRecord(
                string space,
                string spaceRecordHandle,
                string layoutName,
                string layoutHandle,
                int layoutTabOrder,
                string handle,
                int number,
                bool isPaperViewport,
                bool on,
                bool entityVisible,
                bool locked,
                double centerX,
                double centerY,
                double centerZ,
                double paperWidth,
                double paperHeight,
                double viewCenterX,
                double viewCenterY,
                double viewTargetX,
                double viewTargetY,
                double viewTargetZ,
                double viewHeight,
                double customScale,
                double twistAngle,
                double viewDirectionX,
                double viewDirectionY,
                double viewDirectionZ,
                bool nonRectClipOn,
                string nonRectClipHandle,
                LayerState layer,
                List<string> frozenLayers)
            {
                Space = space;
                SpaceRecordHandle = spaceRecordHandle;
                LayoutName = layoutName;
                LayoutHandle = layoutHandle;
                LayoutTabOrder = layoutTabOrder;
                Handle = handle;
                Number = number;
                IsPaperViewport = isPaperViewport;
                On = on;
                EntityVisible = entityVisible;
                Locked = locked;
                CenterX = centerX;
                CenterY = centerY;
                CenterZ = centerZ;
                PaperWidth = paperWidth;
                PaperHeight = paperHeight;
                ViewCenterX = viewCenterX;
                ViewCenterY = viewCenterY;
                ViewTargetX = viewTargetX;
                ViewTargetY = viewTargetY;
                ViewTargetZ = viewTargetZ;
                ViewHeight = viewHeight;
                CustomScale = customScale;
                TwistAngle = twistAngle;
                ViewDirectionX = viewDirectionX;
                ViewDirectionY = viewDirectionY;
                ViewDirectionZ = viewDirectionZ;
                NonRectClipOn = nonRectClipOn;
                NonRectClipHandle = nonRectClipHandle;
                Layer = layer;
                FrozenLayers = frozenLayers;
            }

            public string Space { get; private set; }
            public string SpaceRecordHandle { get; private set; }
            public string LayoutName { get; private set; }
            public string LayoutHandle { get; private set; }
            public int LayoutTabOrder { get; private set; }
            public string Handle { get; private set; }
            public int Number { get; private set; }
            public bool IsPaperViewport { get; private set; }
            public bool On { get; private set; }
            public bool EntityVisible { get; private set; }
            public bool Locked { get; private set; }
            public double CenterX { get; private set; }
            public double CenterY { get; private set; }
            public double CenterZ { get; private set; }
            public double PaperWidth { get; private set; }
            public double PaperHeight { get; private set; }
            public double ViewCenterX { get; private set; }
            public double ViewCenterY { get; private set; }
            public double ViewTargetX { get; private set; }
            public double ViewTargetY { get; private set; }
            public double ViewTargetZ { get; private set; }
            public double ViewHeight { get; private set; }
            public double CustomScale { get; private set; }
            public double TwistAngle { get; private set; }
            public double ViewDirectionX { get; private set; }
            public double ViewDirectionY { get; private set; }
            public double ViewDirectionZ { get; private set; }
            public bool NonRectClipOn { get; private set; }
            public string NonRectClipHandle { get; private set; }
            public LayerState Layer { get; private set; }
            public List<string> FrozenLayers { get; private set; }
        }
    }
}
