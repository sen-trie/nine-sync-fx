float4 tint : DIFFUSE < string Object = "Geometry"; >;
float strength : CONTROLOBJECT < string name = "handle.x"; string item = "Tr"; >;

float4 shade() : COLOR0 {
    return tint * strength;
}

technique Material < string MMDPass = "object"; > {
    pass Surface { PixelShader = compile ps_3_0 shade(); }
}
