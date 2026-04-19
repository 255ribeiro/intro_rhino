import ifcopenshell
import ifcopenshell.api
import rhinoscriptsyntax as rs
import Rhino
import scriptcontext as sc


def get_rhino_ifc_project_data():
    """
    Get all user text from the Rhino document (3dm file properties).
    Returns a dictionary of key-value pairs for IFC project information.
    """
    doc_strings = sc.doc.Strings.DocumentUserStrings
    rhino_ifc_data = {}
    
    for key in doc_strings.AllKeys:
        rhino_ifc_data[key] = doc_strings.Get(key)
    
    return rhino_ifc_data


def get_spatial_container_ifc_data(block_name):
    """
    Get all user text from a block definition (spatial_info block).
    
    Parameters:
    -----------
    block_name : str
        Name of the block
    
    Returns:
    --------
    dict : Dictionary of key-value pairs from block user text with IFC spatial data
    """
    block_def = sc.doc.InstanceDefinitions.Find(block_name)
    if not block_def:
        return {}
    
    rhino_ifc_data = {}
    user_strings = block_def.GetUserStrings()
    
    if user_strings:
        for key in user_strings.AllKeys:
            rhino_ifc_data[key] = user_strings.Get(key)
    
    return rhino_ifc_data


def get_block_transform(block_instance_id):
    """
    Get the location and rotation from a block instance.
    
    Parameters:
    -----------
    block_instance_id : guid
        The GUID of the block instance
    
    Returns:
    --------
    dict : Dictionary with 'location' (x,y,z) and 'rotation' (angles or matrix)
    """
    obj = rs.coercerhinoobject(block_instance_id)
    if not obj or not isinstance(obj, Rhino.DocObjects.InstanceObject):
        return None
    
    xform = obj.InstanceXform
    
    # Extract location
    location = (xform.M03, xform.M13, xform.M23)
    
    # Extract rotation (simplified - you might want full matrix)
    # For now, just return the translation
    transform_data = {
        'location': location,
        'transform_matrix': xform
    }
    
    return transform_data


def find_spatial_info_blocks_in_layer(layer_name):
    """
    Find all 'spatial_info' blocks in a specific layer.
    
    Parameters:
    -----------
    layer_name : str
        The layer name to search
    
    Returns:
    --------
    list : List of block instance GUIDs
    """
    # Get all objects in the layer
    layer_objects = rs.ObjectsByLayer(layer_name)
    if not layer_objects:
        return []
    
    spatial_blocks = []
    for obj_id in layer_objects:
        if rs.IsBlockInstance(obj_id):
            block_name = rs.BlockInstanceName(obj_id)
            if block_name == "spatial_info":
                spatial_blocks.append(obj_id)
    
    return spatial_blocks


def create_ifc_element_from_rhino_ifc_data(ifc_file, rhino_ifc_data, ifc_class=None):
    """
    Creates an IFC element based on Rhino IFC data (key-value pairs).
    
    Parameters:
    -----------
    ifc_file : ifcopenshell.file
        The IFC file object
    rhino_ifc_data : dict
        Dictionary with IFC attributes and custom properties from Rhino
    ifc_class : str (optional)
        Override IFC class name, otherwise uses 'ifcclass' from rhino_ifc_data
    
    Returns:
    --------
    element : IFC entity
        The created IFC element
    """
    
    # Determine IFC class
    if not ifc_class:
        ifc_class = rhino_ifc_data.get('ifcclass', 'IfcBuildingElementProxy')
    
    # Known IFC standard attributes
    standard_attributes = {
        'Name': rhino_ifc_data.get('Name'),
        'Description': rhino_ifc_data.get('Description'),
        'ObjectType': rhino_ifc_data.get('ObjectType'),
        'Tag': rhino_ifc_data.get('Tag'),
        'PredefinedType': rhino_ifc_data.get('PredefinedType'),
        'LongName': rhino_ifc_data.get('LongName'),
        'CompositionType': rhino_ifc_data.get('CompositionType')
    }
    
    # Remove None values (skip optional attributes if not provided)
    standard_attributes = {k: v for k, v in standard_attributes.items() if v is not None}
    
    # Create the IFC element
    element = ifcopenshell.api.run("root.create_entity", 
                                    ifc_file, 
                                    ifc_class=ifc_class,
                                    **standard_attributes)
    
    # Collect remaining custom properties
    reserved_keys = {'ifcclass', 'Name', 'Description', 'ObjectType', 'Tag', 
                     'PredefinedType', 'LongName', 'CompositionType', 
                     'location', 'rotation', 'transform_matrix'}
    
    custom_properties = {k: v for k, v in rhino_ifc_data.items() 
                        if k not in reserved_keys and v is not None}
    
    # Add custom properties as a PropertySet if there are any
    if custom_properties:
        pset = ifcopenshell.api.run("pset.add_pset", 
                                    ifc_file, 
                                    product=element, 
                                    name="CustomProperties")
        
        ifcopenshell.api.run("pset.edit_pset", 
                            ifc_file, 
                            pset=pset, 
                            properties=custom_properties)
    
    return element


def set_spatial_placement(ifc_file, element, location_data):
    """
    Set the placement/location of a spatial element based on transform data.
    
    Parameters:
    -----------
    ifc_file : ifcopenshell.file
        The IFC file object
    element : IFC entity
        The spatial element (IfcSite, IfcBuilding, etc.)
    location_data : dict
        Dictionary containing 'location' tuple (x, y, z)
    """
    if not location_data or 'location' not in location_data:
        return
    
    location = location_data['location']
    
    # Create placement
    # Note: This is simplified. Full implementation would handle rotation too.
    origin = ifc_file.createIfcCartesianPoint((location[0], location[1], location[2]))
    axis = ifc_file.createIfcDirection((0.0, 0.0, 1.0))
    ref_direction = ifc_file.createIfcDirection((1.0, 0.0, 0.0))
    placement = ifc_file.createIfcAxis2Placement3D(origin, axis, ref_direction)
    
    element.ObjectPlacement = ifc_file.createIfcLocalPlacement(None, placement)


def process_spatial_layer(ifc_file, layer_name, parent_spatial=None):
    """
    Process a layer that represents a spatial structure element.
    Looks for spatial_info block and creates corresponding IFC spatial element.
    
    Parameters:
    -----------
    ifc_file : ifcopenshell.file
        The IFC file object
    layer_name : str
        The layer name
    parent_spatial : IFC entity (optional)
        Parent spatial element
    
    Returns:
    --------
    spatial_element : IFC spatial entity
    """
    
    # Find spatial_info blocks in this layer
    spatial_blocks = find_spatial_info_blocks_in_layer(layer_name)
    
    if not spatial_blocks:
        print(f"Warning: No spatial_info block found in layer '{layer_name}'")
        # Create default spatial element based on layer name
        return create_ifc_element_from_rhino_ifc_data(
            ifc_file, 
            {'Name': layer_name}, 
            ifc_class='IfcBuildingStorey'
        )
    
    # Use the first spatial_info block found
    block_id = spatial_blocks[0]
    block_name = rs.BlockInstanceName(block_id)
    
    # Get attributes from block user text
    rhino_ifc_data = get_spatial_container_ifc_data(block_name)
    
    # Get location and rotation from block instance
    transform_data = get_block_transform(block_id)
    if transform_data:
        rhino_ifc_data.update(transform_data)
    
    # Determine IFC class based on layer hierarchy or explicit attribute
    ifc_class = rhino_ifc_data.get('ifcclass', 'IfcBuildingStorey')
    
    # Create spatial element
    spatial_element = create_ifc_element_from_rhino_ifc_data(ifc_file, rhino_ifc_data, ifc_class)
    
    # Set placement based on block location
    if transform_data:
        set_spatial_placement(ifc_file, spatial_element, transform_data)
    
    # Establish spatial hierarchy
    if parent_spatial:
        ifcopenshell.api.run("aggregate.assign_object", 
                            ifc_file, 
                            products=[spatial_element], 
                            relating_object=parent_spatial)
    
    return spatial_element


def build_spatial_hierarchy(ifc_file, project):
    """
    Build the spatial hierarchy from Rhino layer structure.
    Expects nested layers like: Site > Building > Storey
    Each should contain a spatial_info block.
    
    Parameters:
    -----------
    ifc_file : ifcopenshell.file
        The IFC file object
    project : IFC entity
        The IfcProject entity
    
    Returns:
    --------
    dict : Dictionary mapping layer names to IFC spatial elements
    """
    
    spatial_map = {}
    
    # Get all layers
    all_layers = rs.LayerNames()
    
    # Build hierarchy (simplified - assumes specific naming or structure)
    # You might need to adjust this based on your actual layer structure
    
    # Find top-level spatial layers (those without "::" parent indicator)
    root_layers = [layer for layer in all_layers if "::" not in layer]
    
    for root_layer in root_layers:
        # Process as potential site
        site = process_spatial_layer(ifc_file, root_layer, None)
        spatial_map[root_layer] = site
        
        # Link to project
        ifcopenshell.api.run("aggregate.assign_object", 
                            ifc_file, 
                            products=[site], 
                            relating_object=project)
        
        # Find child layers (buildings)
        child_layers = [layer for layer in all_layers 
                       if layer.startswith(root_layer + "::") and 
                       layer.count("::") == 1]
        
        for child_layer in child_layers:
            building = process_spatial_layer(ifc_file, child_layer, site)
            spatial_map[child_layer] = building
            
            # Find grandchild layers (storeys)
            grandchild_layers = [layer for layer in all_layers 
                                if layer.startswith(child_layer + "::") and 
                                layer.count("::") == 2]
            
            for grandchild_layer in grandchild_layers:
                storey = process_spatial_layer(ifc_file, grandchild_layer, building)
                spatial_map[grandchild_layer] = storey
    
    return spatial_map


def rhino_to_ifc_structured(output_path="output.ifc"):
    """
    Main function to convert Rhino document to IFC with proper structure.
    Reads project info from document properties.
    Reads spatial structure from layer hierarchy with spatial_info blocks.
    """
    
    # Get project information from document user text
    project_data = get_rhino_ifc_project_data()
    
    # Create IFC file
    ifc_file = ifcopenshell.api.run("project.create_file")
    
    # Create project with data from document properties
    project_name = project_data.get('ProjectName', 'Unnamed Project')
    project_description = project_data.get('ProjectDescription', None)
    
    project = ifcopenshell.api.run("root.create_entity", 
                                    ifc_file, 
                                    ifc_class="IfcProject", 
                                    name=project_name,
                                    description=project_description)
    
    # Add project custom properties
    reserved_project_keys = {'ProjectName', 'ProjectDescription'}
    project_custom_props = {k: v for k, v in project_data.items() 
                           if k not in reserved_project_keys}
    
    if project_custom_props:
        pset = ifcopenshell.api.run("pset.add_pset", 
                                    ifc_file, 
                                    product=project, 
                                    name="ProjectCustomProperties")
        ifcopenshell.api.run("pset.edit_pset", 
                            ifc_file, 
                            pset=pset, 
                            properties=project_custom_props)
    
    # Build spatial hierarchy from layers
    spatial_map = build_spatial_hierarchy(ifc_file, project)
    
    print(f"Created spatial structure with {len(spatial_map)} elements")
    
    # Now process regular building elements
    objs = rs.GetObjects("Select building elements to export")
    
    if objs:
        for obj in objs:
            # Get object layer
            obj_layer = rs.ObjectLayer(obj)
            
            # Find parent spatial element
            parent_spatial = spatial_map.get(obj_layer)
            
            # Get object Rhino IFC data
            rhino_ifc_data = {}
            keys = rs.GetUserText(obj)
            if keys:
                for key in keys:
                    rhino_ifc_data[key] = rs.GetUserText(obj, key)
            
            # Add object name
            if 'Name' not in rhino_ifc_data:
                obj_name = rs.ObjectName(obj)
                if obj_name:
                    rhino_ifc_data['Name'] = obj_name
            
            # Create IFC element
            element = create_ifc_element_from_rhino_ifc_data(ifc_file, rhino_ifc_data)
            
            # Assign to spatial container
            if parent_spatial:
                ifcopenshell.api.run("spatial.assign_container", 
                                    ifc_file, 
                                    products=[element], 
                                    relating_structure=parent_spatial)
            
            print(f"Created {rhino_ifc_data.get('ifcclass', 'IfcBuildingElementProxy')}: {rhino_ifc_data.get('Name', 'Unnamed')}")
    
    # Save IFC file
    ifc_file.write(output_path)
    print(f"\nIFC file saved to: {output_path}")
    
    return ifc_file


# Run the export
if __name__ == "__main__":
    rhino_to_ifc_structured("rhino_export.ifc")
