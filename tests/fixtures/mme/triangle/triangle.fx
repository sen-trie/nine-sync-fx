float4x4 transform : WORLDVIEWPROJECTION;
texture albedo : MATERIALTEXTURE;
sampler surface = sampler_state { Texture = <albedo>; };

struct Vertex {
    float4 position : POSITION;
    float2 uv : TEXCOORD0;
};

Vertex vs(Vertex input) {
    input.position = mul(input.position, transform);
    return input;
}

float4 ps(Vertex input) : COLOR0 {
    return tex2D(surface, input.uv);
}

technique Object {
    pass Surface {
        VertexShader = compile vs_3_0 vs();
        PixelShader = compile ps_3_0 ps();
    }
}
